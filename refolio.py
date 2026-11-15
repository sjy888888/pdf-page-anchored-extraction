# -*- coding: utf-8 -*-
"""refolio.py —— 从 OCR jsonl 缓存重新判定印刷页码，并重写 txt

为什么需要它（2026-11-11 实测）：
    ocr_pdf_paged.py 的 detect_folio 只认两种形态：
      ① 整行纯数字（页脚常见）
      ② 行首 2–4 位数字 + 非数字（「18新版序言」）
    但《哲学笔记》的页眉是「页码 + 篇名」粘在一起的，奇数页页码在行首，
    偶数页页码在**行尾**：
        「47马克思和恩格斯《神圣家族》一书摘要」
        「马克思和恩格斯《神圣家族》一书摘要23」
    行尾那个 23 抓不到 → 偶数页全判 None → 页码率掉到 11.8%。

它做的事：
    1. 读 .ocr.jsonl（每页的 lines 都在，不用重新 OCR）
    2. 重新找页码候选：页脚区 / 页眉区 行首 + **行尾**
    3. 整卷投票定众数偏移 mode_delta
    4. 只保留与偏移一致的页码，其余降级为【p?|pdfN】
    5. 重写 txt（字段口径与 ocr_pdf_paged.py 一致）

用法：
    python refolio.py <jsonl> <out.txt> --book "书名" --vol "版本" [--tol 12] [--dry]

★ 不猜：找不到与偏移一致的页码就写【p?|pdfN】，绝不用算术硬补。
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

FULL2HALF = {ord(c): ord(h) for c, h in zip("０１２３４５６７８９", "0123456789")}
NUM_ONLY = re.compile(r"^(\d{1,4})$")


def line_text(ln):
    return "".join(t for t in ln).translate(FULL2HALF).strip()


# 2026-11-11（哲学笔记实测）：OCR 把数字 1 读成字母 i / l / I 很常见。
#   实例：PDFp36 页眉真值「…一书摘要15」，OCR 出「…一书橘要i5」；
#         PDFp38 真值「…摘要17」，OCR 出「…书摘要7」（1 整个丢了）。
#   只在**行尾**做这个替换（页眉行尾那一串就是页码），不动正文。
LEAD_LIKE = str.maketrans({"i": "1", "l": "1", "I": "1", "|": "1"})


def tail_number(txt):
    """从行尾取页码，容忍 OCR 把 1 认成 i/l。返回 int 或 None。"""
    m = re.search(r"(?<=[^\d\s])\s*([\dilI|]{1,4})$", txt)
    if not m:
        return None
    raw = m.group(1).translate(LEAD_LIKE)
    if raw.isdigit():
        v = int(raw)
        return v if 1 <= v <= 2000 else None
    return None


def find_candidates(lines, n_lines=4):
    """页眉/页脚区里可能是印刷页码的数。

    返回 [(值, 强弱)]，强=2 行首粘连 / 整行纯数字，弱=1 行尾粘连。

    ★ 2026-11-11 第二次修（差点又造假页码）：
      第一版把「前 3 行 / 后 4 行」当成页眉页脚区，结果抓到了**正文里引用的页码**。
      实测反例：
        PDFp700 页眉「人名索引679」，页脚正文「…349、432、598.」
                → 抓出 598，被判成本页页码。真值 679。
      所以现在只从页眉/页脚取，且**页码必须与篇名粘连或整行纯数字**，
      正文里的孤立数字一律不取。宁可少判，不许错判。

    ★ 2026-11-11 第四次修（强弱分级）：
      实测 PDFp45 页眉是「34马克思和恩格斯《神圣家族》一书摘要」，
      行首 34 才是真页码（偏移 21 对它不成立，因为那是另起的篇）；
      而 PDFp528 页眉是「阿贝尔·莱伊…一书批注509」，行尾 509 才是真页码。
      两种都存在，但**行首粘连比行尾粘连可靠**——行尾容易吃到篇名里的数字。
      所以给强弱，选的时候强优先。
    """
    cand = []
    if not lines:
        return cand

    head = lines[:3]
    foot = lines[-n_lines:]
    for ln in head + foot:
        txt = line_text(ln)
        if not txt:
            continue
        # 2026-11-11：说明/序言/目录常用罗马数字编页（「说明：VI」「前言 ⅩⅡ」）。
        # 这类页**不按阿拉伯页码判**——除非页眉同时给了阿拉伯数字，
        # 否则宁可判无页码，也不许把罗马数字旁边别的数当成页码。
        # 实测反例：PDFp10 页眉「说明：VI」，被判成 p1，而真值根本不进阿拉伯序列。
        if re.search(r"(?<![A-Za-z])[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+(?![A-Za-z])", txt) or \
           re.search(r"(?<![\d.])\b[IVXLC]{1,6}\b", txt) and not re.search(r"\d", txt):
            continue
        # ① 整行纯数字 —— 强
        m = NUM_ONLY.match(txt)
        if m:
            cand.append((int(m.group(1)), 2))
            continue
        # 2026-11-12 加：**「·6·」「7·」「.·15·」「·16。」这类点号包围的页码**。
        #   实测（衣俊卿集，469 页）：页码全印成 `·N·`，OCR 读出 405 页有值，
        #   但 refolio 只认了 10 页（2.1%）——因为：
        #     ① 不是「整行纯数字」（含点号）
        #     ② 不是「行首数字」（首字符是点）
        #     ③ 行尾匹配只对页眉做，而它印在页脚
        #   判据：去掉空白和点号后是 1-4 位纯数字，且原串长度 ≤8。
        #   放行强度 2（和整行纯数字同级）——因为形态明确、不会撞上正文注文。
        m = re.fullmatch(r"[\s.·．]{0,3}(\d{1,4})[\s.·．。]{0,3}", txt)
        if m:
            cand.append((int(m.group(1)), 2))
            continue
        # ② 行首数字 + 紧跟非数字：「86黑格尔《逻辑学》一书摘要」「305谈谈辩证法问题」—— 强
        m = re.match(r"^(\d{1,4})\s*(?=[^\d\s])", txt)
        if m:
            cand.append((int(m.group(1)), 2))
        # ③ 行尾数字 —— 弱
        #    ★ 只对**页眉**做行尾匹配。页脚正文太杂，行尾粘连会从注文里刮出假页码。
        #    实测反例：PDFp26 页脚「指《马克思恩格斯全集》第2卷的页码…编者注」，
        #    行尾匹配刮出一个数，把整页判成 p1（真值是 p5 左右）。
        #    页脚的可信形态只有 ①「整行就是页码」。
        if ln in head:
            v = tail_number(txt)
            if v is not None:
                cand.append((v, 1))

    # 去重，保留最强的那一档
    out = {}
    for v, w in cand:
        if 1 <= v <= 2000 and v not in out:
            out[v] = w
        elif v in out and w > out[v]:
            out[v] = w
    return sorted(out.items(), key=lambda x: (-x[1], x[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("out")
    ap.add_argument("--book", default="")
    ap.add_argument("--vol", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--tol", type=int, default=12,
                    help="|pdf页 − 印刷页 − mode_delta| 容许上限（默认 12）")
    ap.add_argument("--dry", action="store_true", help="只报统计，不写文件")
    args = ap.parse_args()

    recs = []
    with open(args.jsonl, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                recs.append(json.loads(ln))
            except Exception:
                pass
    recs.sort(key=lambda r: r["pdf_page"])
    if not recs:
        print("[refolio] jsonl 是空的", file=sys.stderr)
        sys.exit(1)

    # 每页重新找候选
    per_page = {}
    for r in recs:
        per_page[r["pdf_page"]] = find_candidates(r.get("lines") or [])

    # 整卷投票：哪一对 (pdf页, 候选值) 的差值最常见
    # 强弱要计入票权：行首粘连/整行纯数字（强=2）比行尾（弱=1）更可信，
    # 否则篇名里带的数字会把众数带偏。
    deltas = Counter()
    for p, cand in per_page.items():
        for v, w in cand:
            d = p - v
            if -50 <= d <= 400:          # 页码偏移不可能是天文数字
                deltas[d] += w
    mode_delta = deltas.most_common(1)[0][0] if deltas else None
    if mode_delta is None:
        print("[refolio] 一票都没有，全卷按无页码处理", file=sys.stderr)

    # 逐页定页码：只认与 mode_delta 一致的
    # 2026-11-11：旧写法取「第一个落在 tol 内的候选」，实测会出假页码——
    #   PDFp38 的页眉 OCR 成「…一书摘要7」（真值 17 掉了 1），
    #   候选 7 的偏差 |38-7-21|=10 也 ≤ tol 12，于是被判成 p7（真值 p17）。
    #   改为**取偏差最小的那个**，并把偏差记下来供事后抽查。
    #
    # 2026-11-11 第三次修（单调性闸门）：
    #   只靠「偏差最小」还不够。实测 PDFp26 会抓到 p1（真值约 p5 ~ p7），
    #   偏差 |26-1-21|=4 很小，但它是错的——印刷页码不可能从 p16 掉回 p1。
    #   一本书的印刷页码对 PDF 页序**必须单调不减**（局部允许原地，不允许倒退）。
    #   所以按页序走一遍，只接受不小于上一页已定页码的候选。
    measured, unmeasured, examples = 0, 0, []
    near_miss = []            # 偏差非 0 的页：页码可信度降一级
    fixed = []
    last_printed = 0          # 单调性闸门：上一页定下的印刷页码
    last_pdf = 0              # 上一页的 PDF 页序
    for r in recs:
        p = r["pdf_page"]
        # 2026-11-11 试过「printed 非空就直接采用」，实测更差：
        #   海德格尔 87.7% → 62.9%，且 PDFp3/PDFp4 被安上同一个 p20。
        #   根因：fix_header_folio 补进来的 printed 有 8% 是错的（跨章节页码重置），
        #   直接采用等于把错值当天条。已回退，保持原逻辑。
        best = None               # (printed, dev)
        if mode_delta is not None:
            for v, w in per_page.get(p, []):
                dev = abs((p - v) - mode_delta)
                if dev > args.tol:
                    continue
                if v < last_printed:      # 倒退 → 不可能，丢掉
                    continue
                # 同一页码不许横跨太多页。实测 PDFp10 和 PDFp26 都被判成 p1，
                # 真值分别是 p1 和 p5 左右——p1 被判给了两页。
                # 一个印刷页码最多覆盖相邻 2 个 PDF 页（双面扫描偶尔一页印、一页不印）。
                if v == last_printed and p - last_pdf > 2:
                    continue
                if best is None or dev < best[1]:
                    best = (v, dev)
        printed, best_dev = (best if best else (None, None))
        if printed:
            measured += 1
            last_printed = printed
            last_pdf = p
            if best_dev:
                near_miss.append((p, printed, best_dev))
            if len(examples) < 6:
                examples.append((p, printed))
        else:
            unmeasured += 1
        fixed.append((p, printed, r.get("lines") or []))

    total = len(fixed)
    ratio = measured / total if total else 0
    print(f"[refolio] 页数 {total}｜判定到页码 {measured}｜无 {unmeasured}"
          f"｜页码率 {ratio:.1%}｜众数偏移 {mode_delta}")
    if examples:
        print("[refolio] 样例 " + "  ".join(f"PDFp{p}→p{v}" for p, v in examples))
    if near_miss:
        print(f"[refolio] ⚠ 偏差≠0 的页 {len(near_miss)} 个（引前必核）："
              + "  ".join(f"PDFp{p}→p{v}(±{d})" for p, v, d in near_miss[:12]))

    if args.dry:
        return

    import datetime
    header = [
        "=" * 74,
        f"著作名：{args.book}",
        f"出处卷次：{args.vol}",
        f"来源文件：{Path(args.jsonl).name.replace('.ocr.jsonl', '')}",
        f"来源 URL：{args.source}",
        f"处理日期：{datetime.date.today()}",
        "处理方式：**OCR**（RapidOCR，170dpi 灰度渲染）。原 PDF 无文字层。",
        "字源：OCR 识别结果，未经人工逐字校订；错字按原样保留，不静默改字。",
        "页码体例：【pN】= 页眉/页脚区实测印刷页码；【p?|pdfN】= 无页码，只记 PDF 页序。",
        f"页码实测率：{measured}/{total} = {ratio:.1%}；众数偏移（PDF页−印刷页）= {mode_delta}。",
        "本次页码由 refolio.py 重判（同时认页眉行首与行尾数字）。",
        "引用规则：只有【pN】的页码可直接引；【p?】的段落引用写「页码待核」。",
        "=" * 74,
    ]

    parts = []
    for p, printed, lines in fixed:
        tag = f"【p{printed}】" if printed else f"【p?|pdf{p}】"
        parts.append(f"{tag}[PDFp{p}]")
        if not lines:
            parts.append("〔本页 OCR 无结果：插图/空白页，待人工核〕")
        else:
            parts.extend(lines)
        parts.append("")

    Path(args.out).write_text("\n".join(header) + "\n\n" + "\n".join(parts),
                              encoding="utf-8")

    # pages.json 同步
    idx = [{"pdf_page": p, "printed": pr,
            "first": (lines[0][:60] if lines else "")}
           for p, pr, lines in fixed]
    Path(args.out + ".pages.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[refolio] 已写 {args.out}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ocr_jsonl2txt.py —— 把 .ocr.jsonl 缓存转成标准 txt（带页码标记）

2026-11-10 发现：库里有一批书**OCR 已经跑完，但没有转成 txt**。
  例：卢卡奇《历史与阶级意识》353 页扫完了，只有 .ocr.jsonl，没有 .txt。
  原因：ocr_batch.py 只负责 OCR 到缓存，转换是另一步，之前断了。

本脚本按库里统一的页码体例输出：
  【pN】[PDFpN]   —— N 是印刷页码（printed 有值）
  [PDFpN]         —— 印刷页码不可信或没有时，只留 PDF 页序（宁无不假）

用法：
  python ocr_jsonl2txt.py <jsonl> <输出txt> --book 书名 --vol 版本 --layer L2
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--book", default="")
    ap.add_argument("--vol", default="")
    ap.add_argument("--layer", default="")
    ap.add_argument("--source", default="本机 PDF（OCR）")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    rows = []
    for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue

    rows.sort(key=lambda r: r.get("pdf_page", 0))

    # 2026-11-10 补：从页首/页尾行里抽印刷页码。
    #   事故：卢卡奇《历史与阶级意识》OCR 后 printed 全是 None（0%），
    #   但页码其实就在页眉里，只是和标题粘连：
    #     「什么是正统马克思主义？ 58」   ← 页码在右
    #     「50什么是正统马克思主义？」     ← 页码在左
    #     「56什么是正统马克默主义！」     ← OCR 把「思」认成「默/想/亡」
    #   判据：行内的数字必须**贴着行首或行尾**，且 1–1500，且该行不是纯数字行
    #        （纯数字行是正文里的数字，不是页码）。
    BOOKNUM = re.compile(r"^[^\d]{2,24}?\s(\d{1,4})\s*$|^\s*(\d{1,4})\s*[^\d]{2,24}$")

    def sniff(r):
        ls = [str(x) for x in (r.get("lines") or [])]
        cand = ls[:1] + ls[-1:] if len(ls) > 1 else ls
        for ln in cand:
            m = BOOKNUM.match(ln.strip())
            if not m:
                continue
            v = int(m.group(1) or m.group(2))
            if 1 <= v <= 1500:
                return v
        return None

    sniffed = 0
    for r in rows:
        if r.get("printed") is None:
            v = sniff(r)
            if v is not None:
                r["printed"] = v
                r["_sniffed"] = True
                sniffed += 1

    # 2026-11-10 补：**修「十位丢失」**。
    #   事故：卢卡奇《历史与阶级意识》OCR 把两位数的十位吃掉：
    #     pdf72 → 56（对）  pdf74 → 3（应为 58）  pdf82 → 6（应为 62）
    #   表现：Δ 突然变成 -53、-54 这种跳变。
    #   判据：以已确认的稳定步长（连续 3 次相同的 Δ）为基准，
    #        某个值若「补上十位/百位后正好接上」，就补回去。
    #        补完还要过连续性检查；补不对就照旧丢掉。
    fixed = 0
    seq = [(r["pdf_page"], r.get("printed")) for r in rows if r.get("printed") is not None]
    # 先估步长（正文页 pdf 步长几乎恒定）
    from collections import Counter
    steps = Counter()
    for (p1, v1), (p2, v2) in zip(seq, seq[1:]):
        dp, dv = p2 - p1, v2 - v1
        if dp > 0 and 0 < dv <= dp * 3:
            steps[dv / dp] += 1
    step = steps.most_common(1)[0][0] if steps else 1.0
    # 逐页修
    prev = None
    for r in rows:
        v = r.get("printed")
        if v is None:
            continue
        if prev is not None:
            dp = r["pdf_page"] - prev[0]
            expect = prev[1] + dp * step
            if abs(v - expect) > 2:
                # 试补十位/百位
                for k in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
                    if abs(v + k - expect) <= 2:
                        r["printed"] = v + k
                        fixed += 1
                        v = v + k
                        break
        prev = (r["pdf_page"], v)

    # 页码双轨制：printed 要过连续性检查，回退/跳号就丢掉
    bad = 0
    prev = None
    prev_p = None
    for r in rows:
        v = r.get("printed")
        if v is None:
            prev = None
            continue
        try:
            v = int(v)
        except Exception:
            r["printed"] = None
            prev = None
            continue
        if prev is not None:
            gap = r["pdf_page"] - prev_p
            if v < prev or v > prev + gap + 3:
                r["printed"] = None
                bad += 1
                prev = None
                prev_p = r["pdf_page"]
                continue
        prev, prev_p = v, r["pdf_page"]

    # 全卷混乱率
    measurable = sum(1 for r in rows if r.get("printed") is not None) + bad
    chaos = bad / measurable if measurable else 0
    downgraded = chaos > 0.05
    if downgraded:
        for r in rows:
            r["printed"] = None

    n_pn = sum(1 for r in rows if r.get("printed") is not None)
    n_all = len(rows)

    header = [
        "=" * 74,
        f"著作名：{args.book}",
        f"出处卷次：{args.vol}",
        f"来源文件：{src.stem}（OCR 共 {n_all} 页）",
        f"来源 URL：{args.source}",
        f"抽取日期：{date.today().isoformat()}",
        "字源：PDF 扫描件经 RapidOCR 识别；**未经人工校订**，错字按原样保留。",
        "页码双轨：【pN】= 印刷页码（可信）；[PDFpN] = PDF 页序（永远可靠）。",
        f"页码实测率：{n_pn}/{n_all} = {n_pn/n_all*100:.1f}%",
        f"序列连续性：剔除 {bad} 个回退/跳号页码，全卷混乱率 {chaos:.1%}"
        + ("，**已整卷降级**（只有 PDF 页序）。" if downgraded else "（≤5%，逐页保留）。"),
        "引用规则：只有【pN】可直接引；没有【pN】的页，页码位留空自己核，**不许编号。**",
        "=" * 74,
    ]

    parts = []
    for r in rows:
        p = r["pdf_page"]
        v = r.get("printed")
        tag = f"【p{v}】" if v is not None else ""
        parts.append(f"{tag}[PDFp{p}]")
        body = r.get("lines") or []
        parts.extend(str(x) for x in body)
        parts.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + [""] + parts), encoding="utf-8")

    chars = sum(len("".join(str(x) for x in (r.get("lines") or []))) for r in rows)
    print(f"{out.name}: {chars:,} 字，{n_all} 页，页码 {n_pn}/{n_all} = {n_pn/n_all*100:.1f}%"
          + ("  ⚠整卷降级" if downgraded else ""))


if __name__ == "__main__":
    main()

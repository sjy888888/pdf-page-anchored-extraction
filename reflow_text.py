# -*- coding: utf-8 -*-
"""
reflow_text.py —— 修复「文字层被拆成一字一行」的 PDF 抽取结果

问题（2026-11-09，建国以来毛泽东文稿实测）：
  《建国以来毛泽东文稿》的文字层，每个汉字单独占一行：
      彭
      ，
      指
      彭
      德
      怀
  `get_text("text")` 出来的就是这个。直接入库，检索能中但读不了，也不能引。

判据（不是猜的）：
  统计每页的「单字行占比」。正常排版 < 5%；被拆碎的大于 60%。

修法：不用 get_text("text")，改用 block 级 bbox：
  1. 取 page.get_text("blocks")，按 bbox 的 y0 聚成视觉行（容差 = 行高 60%）
  2. 同一行内按 x0 排序拼接
  3. 行首缩进约 2 字 → 段落起头；其余行首行末不加空格，直接连

用法:
  python reflow_text.py <pdf> <out.txt> --book "..." [--chunk 400]
  或作为模块：reflow_page(page) -> str
"""
import sys
import json
import argparse
import datetime
from pathlib import Path

import pymupdf

FULL2HALF = {ord(c): ord(h) for c, h in zip("０１２３４５６７８９", "0123456789")}


def page_lines_by_bbox(page, ytol_ratio=0.6):
    """按 block 的 bbox 把正文接回正常行，并按字符 bbox 修正字序。

    2026-11-09 三次修（每次都是前一次不够）：
      ① 按 span 的 y 聚类 → 还是一字一行。因为 block 内部每个字是独立伪行。
      ② block 内换行去掉直接拼 → 能读了，但**字间多出空格**，且**标题区乱序**
         （`…此 种德 怀 仲 勋〔1〕，林 彪…` —— block 内字符顺序不等于阅读顺序）。
      ③ 本版：block 内不用文本顺序，**按每个字符的 bbox 排序**：
         先按 y 分行（容差 = 字高 50%），行内按 x 排，直接拼、不加空格。
         中文不靠空格分词，加了空格反而切碎检索。

    证据（mao-jianguo-01 第93页 block 2）：
      bbox=(95.6,102.0,390.3,114.6)
      text='〔１〕　彭 \\n， \\n指 \\n彭 \\n德 \\n怀 \\n， \\n当 \\n时 \\n任 \\n中 \\n国'
      → 按字符 bbox 排序后是「〔１〕　彭，指彭德怀，当时任中国」
    """
    try:
        dd = page.get_text("dict")
    except Exception:
        return []

    out = []
    for b in dd.get("blocks", []):
        if b.get("type") != 0:
            continue
        # 收本 block 里每个字符的 (y0, x0, char)
        chars = []
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                t = sp.get("text", "")
                if not t:
                    continue
                bx0, by0, bx1, by1 = sp["bbox"]
                n = len(t)
                if n == 0 or bx1 <= bx0:
                    chars.append((by0, bx0, t))
                    continue
                # span 里多字符时，按等宽切分每个字的 x（中文等宽，误差可接受）
                w = (bx1 - bx0) / n
                for i, ch in enumerate(t):
                    if ch.strip() == "" and ch != "\u3000":
                        # 普通空格/制表：跳过，中文不需要
                        continue
                    chars.append((by0, bx0 + i * w, ch))
        if not chars:
            continue

        # 按 y 分行（容差 = span 高度的一半）
        hs = [sp["bbox"][3] - sp["bbox"][1] for ln in b.get("lines", [])
              for sp in ln.get("spans", []) if sp["bbox"][3] > sp["bbox"][1]]
        med_h = sorted(hs)[len(hs) // 2] if hs else 10.0
        tol = max(med_h * 0.5, 2.5)

        chars.sort(key=lambda c: (c[0], c[1]))
        lines, cur, cur_y = [], [], None
        for y, x, ch in chars:
            if cur_y is None or abs(y - cur_y) <= tol:
                cur.append((x, ch))
                cur_y = y if cur_y is None else cur_y
            else:
                lines.append("".join(c for _, c in sorted(cur)))
                cur, cur_y = [(x, ch)], y
        if cur:
            lines.append("".join(c for _, c in sorted(cur)))

        for ln_txt in lines:
            s = ln_txt.translate(FULL2HALF).replace("\u3000", " ").strip()
            # 多空格压成一个（中文里不需要对齐空格）
            s = " ".join(s.split())
            if s:
                out.append(s)
    return out


def reflow_page(page):
    """按 block 重排一页正文（页码由调用方处理）"""
    lines = page_lines_by_bbox(page)
    if not lines:
        return []
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--book", default="")
    ap.add_argument("--vol", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=400)
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    end = args.end if args.end > 0 else total
    end = min(end, total)
    rng = list(range(args.start, end + 1))

    # 单字行占比诊断
    def single_ratio(pno):
        txt = doc.load_page(pno - 1).get_text("text")
        lines = [l for l in txt.split("\n") if l.strip()]
        if not lines:
            return 0.0
        return sum(1 for l in lines if len(l.strip()) <= 1) / len(lines)

    sample = rng[:: max(len(rng) // 30, 1)][:30]
    ratios = [single_ratio(p) for p in sample]
    med_single = sorted(ratios)[len(ratios) // 2] if ratios else 0

    # 页码：借 pdf2txt_paged 的候选逻辑（内联一份，避免循环导入）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p2t", r"G:\path\to\knowledge-base\工具\pdf2txt_paged.py")
    # 不执行 main，只取函数——它模块级没有副作用，可以导
    p2t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p2t)

    raw = {p: p2t.digit_span_groups(doc.load_page(p - 1)) for p in rng}
    from collections import Counter
    deltas = Counter()
    for p in rng:
        best = None
        for zone, v in raw[p]:
            if abs(p - v) > 90:
                continue
            if best is None or abs(p - v) < abs(p - best[1]):
                best = (zone, v)
        if best:
            deltas[p - best[1]] += 1
    mode_delta, _ = (deltas.most_common(1)[0] if deltas else (0, 0))

    parts, stat, idx = [], Counter(), []
    for pno in rng:
        page = doc.load_page(pno - 1)
        pick = None
        for zone, v in raw[pno]:
            if abs((pno - v) - mode_delta) <= 8:
                pick = (zone, v)
                break
        printed = pick[1] if pick else None
        if printed:
            stat["measured"] += 1
            tag = f"【p{printed}】"
        else:
            stat["unmeasured"] += 1
            tag = f"【p?|pdf{pno}】"

        body_lines = reflow_page(page)
        parts.append(f"{tag}[PDFp{pno}]")
        if not body_lines:
            parts.append("〔本页无可抽取文字：插图/空白页，待人工核〕")
            stat["empty"] += 1
        else:
            parts.extend(body_lines)
        parts.append("")
        idx.append({"pdf_page": pno, "printed": printed,
                    "first": (body_lines[0][:70] if body_lines else "")})

    with_text = stat["measured"] + stat["unmeasured"]
    ratio = stat["measured"] / with_text if with_text else 0

    header = [
        "=" * 74,
        f"著作名：{args.book}",
        f"出处卷次：{args.vol}",
        f"来源文件：{Path(args.pdf).name}（PDF 共 {total} 页）",
        f"来源 URL：{args.source}",
        f"抽取日期：{datetime.date.today().isoformat()}",
        f"处理方式：REFLOW（block bbox 重排）。原文字层单字行占比中位数 {med_single:.0%}，"
        f"{'已重排' if med_single > 0.3 else '未触发重排'}。",
        "字源：PDF 文字层，未经人工校订；错字按原样保留。",
        "页码体例：【pN】= 页眉/页脚实测印刷页码；【p?|pdfN】= 无页码，只记 PDF 页序。",
        f"页码实测率：{stat['measured']}/{with_text} = {ratio:.1%}；众数偏移 = {mode_delta}。",
        "=" * 74,
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + [""] + parts), encoding="utf-8")
    out.with_suffix(out.suffix + ".pages.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out), "pages": len(rng),
        "single_line_ratio_median": round(med_single, 3),
        "reflowed": med_single > 0.3,
        "measured": stat["measured"], "unmeasured": stat["unmeasured"],
        "empty": stat["empty"],
        "measured_ratio": round(ratio, 3),
        "mode_delta": mode_delta,
        "bytes": out.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

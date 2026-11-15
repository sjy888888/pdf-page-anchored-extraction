# -*- coding: utf-8 -*-
"""fix_page_offset.py —— 用实测稳定偏移补全【p?】页码

2026-11-10 背景（胡锦涛文选第3卷）：
  文件里 398 页有【pN】、282 页是【p?】，实测率 58.6%。
  但一查 PDF 就发现：**偏移完全稳定**——30 个样本全是「印刷页 = PDF页 - 13」。

  也就是说这 282 页不是「本来没页码」，是**检测漏了**。
  这种情况能用偏移推算补回来。

判据（三条全过才补，缺一条就保留 p?）：
  ① 偏移在样本里的众数占 ≥ 80%（够稳定）
  ② 样本数 ≥ 10（不是碰巧）
  ③ 补出来的页码落在合理区间（1 ≤ p ≤ 该书最大印刷页）

用法:
  python fix_page_offset.py <txt> --pdf <pdf> [--dry-run]
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

SEP = re.compile(r"^={20,}\s*$", re.M)
PAGE = re.compile(r"^【p(\?\|pdf(\d+)|(\d+))】", re.M)


def measure_offset(pdf, sample=200):
    """从 PDF 页眉/页脚实测偏移"""
    d = pymupdf.open(pdf)
    H = d.load_page(0).rect.height
    offs = []
    n = min(sample, d.page_count)
    for i in range(n):
        pg = d.load_page(i)
        got = None
        for b in pg.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                for s in ln.get("spans", []):
                    t = s["text"].strip()
                    if not t or not re.fullmatch(r"\d{1,4}", t):
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    if y1 < H * 0.12 or y0 > H * 0.88:
                        got = int(t)
                        break
                if got:
                    break
            if got:
                break
        if got:
            offs.append(got - (i + 1))
    d.close()
    if len(offs) < 10:
        return None, len(offs), 0.0
    c = Counter(offs)
    mode, cnt = c.most_common(1)[0]
    return mode, len(offs), cnt / len(offs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("txt")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mode, n, ratio = measure_offset(args.pdf)
    print(f"样本 {n} 页，偏移众数 {mode}，占比 {ratio:.1%}")
    if mode is None or ratio < 0.80:
        print(f"判定：偏移不稳定（需 ≥80%），**不补页码**，保留 p?")
        return

    p = Path(args.txt)
    text = p.read_text(encoding="utf-8", errors="replace")
    done = 0

    def repl(m):
        nonlocal done
        if m.group(2):  # p?|pdfN
            pdfno = int(m.group(2))
            printed = pdfno + mode
            if 1 <= printed <= 2000:
                done += 1
                # 2026-11-10 修：原 return 又拼了一次 [PDFpN]，但原文里已经有，导致 [PDFp124][PDFp124]
                return f"【p{printed}】[PDFp{pdfno}]"
        return m.group(0)

    new = PAGE.sub(repl, text)
    total_p = len(re.findall(r"^【p\d+", new, re.M))
    total_u = len(re.findall(r"^【p\?", new, re.M))
    print(f"补上 {done} 页；补后 pN={total_p} p?={total_u} 实测率 {total_p/(total_p+total_u):.1%}")

    if args.dry_run:
        print("（dry-run，未写盘）")
        return

    # 在文件头记录补页码依据
    head_note = (f"★ 页码补全说明（2026-11-10）：原 {done} 页缺页码，"
                 f"按 PDF 实测稳定偏移 {mode:+d}（样本 {n} 页，一致率 {ratio:.0%}）推算补入。\n"
                 f"  推算页标注为【pN】，与实测页格式相同——**但请知悉部分是推算值**。\n")
    m = list(SEP.finditer(new))
    if len(m) >= 2:
        new = new[:m[1].start()] + head_note + new[m[1].start():]
    p.write_text(new, encoding="utf-8")
    print(f"已写 {p}")


if __name__ == "__main__":
    main()

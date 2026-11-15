# -*- coding: utf-8 -*-
"""
calib_offset.py —— 自动标定「PDF 页序号 → 印张页码」的偏移
方法：在每页的页首若干行 / 页尾若干行里找纯数字行（页眉/页脚页码），
      与 PDF 页号做众数投票，取 offset = PDF页 - 印刷页 的出现最多者。
用法: python calib_offset.py <pdf> [--sample 120] [--json]
"""
import re
import sys
import json
import argparse
from collections import Counter
from pathlib import Path

import pymupdf

NUM = re.compile(r"^\s*[·•\-\s]*(\d{1,4})[·•\-\s]*$")


def page_candidates(txt):
    lines = [l.strip() for l in txt.split("\n")]
    lines = [l for l in lines if l]
    if not lines:
        return []
    cands = []
    # 页首 3 行 / 页尾 3 行
    for l in lines[:3] + lines[-3:]:
        m = NUM.match(l)
        if m:
            cands.append(int(m.group(1)))
    # 页首形如 「第三章xxx  123」 或 「123 第四章xxx」
    for l in lines[:2]:
        m = re.match(r"^(\d{1,4})\s*\S", l)
        if m:
            cands.append(int(m.group(1)))
        m = re.search(r"\s(\d{1,4})$", l)
        if m:
            cands.append(int(m.group(1)))
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    # 跳开前后各 20 页（封面/版权/目录，页码不规律），在正文区间均匀取样
    lo = 21
    hi = total - 15
    if hi <= lo:
        lo, hi = 1, total
    step = max((hi - lo) // args.sample, 1)
    votes = Counter()
    detail = []
    for p in range(lo, hi + 1, step):
        txt = doc.load_page(p - 1).get_text("text")
        cands = page_candidates(txt)
        for c in cands:
            d = p - c
            if 0 <= d <= 60:
                votes[d] += 1
        if cands:
            detail.append({"pdf": p, "printed": cands, "pick": None})

    if not votes:
        print(json.dumps({"ok": False, "reason": "无法从页眉页脚取到页码", "total": total}, ensure_ascii=False))
        sys.exit(1)

    best, cnt = votes.most_common(1)[0]
    second = votes.most_common(2)[1] if len(votes) > 1 else (None, 0)
    out = {
        "ok": True,
        "pdf": Path(args.pdf).name,
        "total_pages": total,
        "offset": best,
        "offset_votes": cnt,
        "sampled_pages": len(detail),
        "runner_up": {"offset": second[0], "votes": second[1]},
        "confidence": round(cnt / max(len(detail), 1), 3),
        "note": "印张页 = PDF 页 - offset",
    }
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()

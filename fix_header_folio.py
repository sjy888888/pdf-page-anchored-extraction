# -*- coding: utf-8 -*-
"""fix_header_folio.py —— 从页眉补页码（左右页交替的那种）

2026-11-11 起因（海德格尔选集）：
    1392 页，页码率只 44.9%。查下来规律非常清楚：

        偶数页  printed=42    首行「42第一编前期存在论哲学」    ← 页码在行首，能识别
        奇数页  printed=None  首行「存在与时间41」              ← 页码在书名后面，识别不到

    ocr_pdf_paged.py 的 detect_folio() 只认「行首纯数字」和「页脚纯数字」，
    奇数页的「存在与时间41」两边都不是纯数字，所以整批漏掉。

判据（必须三条同时成立才采用）：
    ① 该页没有 printed
    ② 首行末尾能取到 1-4 位数字
    ③ 这个数字 == 前面最近一个已知页码 + 1（允许跨 1-3 页的空档）

实测效果（海德格尔选集）：
    能连上 553 页，连不上 50 页（8%），无数字 94 页
    连不上的多是「跨章节」「目录页」——那些本来页码就重置，不算错

用法：
    python fix_header_folio.py --jsonl <xx.jsonl> --dry
    python fix_header_folio.py --jsonl <xx.jsonl> --apply
"""
import argparse
import json
import re
import sys
from pathlib import Path

TAIL_NUM = re.compile(r"(\d{1,4})\s*$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--window", type=int, default=5,
                    help="往回找已知页码的最大页数")
    args = ap.parse_args()

    p = Path(args.jsonl)
    recs = [json.loads(l) for l in p.open(encoding="utf-8")]
    print(f"读入 {len(recs)} 页")
    have = sum(1 for r in recs if r.get("printed"))
    print(f"  已有 printed {have} 页（{have/len(recs):.0%}）")

    fixed = {}
    for i, r in enumerate(recs):
        if r.get("printed"):
            continue
        ls = [l.strip() for l in r["lines"] if l.strip()]
        if not ls:
            continue
        m = TAIL_NUM.search(ls[0])
        if not m:
            continue
        cand = int(m.group(1))
        if not (1 <= cand <= 3000):
            continue
        # 往回找最近的已知页码
        prev = None
        for j in range(i - 1, max(-1, i - args.window), -1):
            if recs[j].get("printed"):
                prev = int(recs[j]["printed"])
                break
        if prev is None:
            continue
        # 允许跨 1-3 页空档
        if prev + 1 <= cand <= prev + 4:
            fixed[i] = (cand, ls[0][:48])

    print(f"\n可补页码 {len(fixed)} 页")
    print("前 10 例：")
    for i, (n, line) in list(fixed.items())[:10]:
        print(f"   PDFp{recs[i]['pdf_page']:<5} → p{n:<4}  行={line!r}")

    if args.dry or not args.apply:
        print("\n（未写入，加 --apply 才改）")
        return

    for i, (n, _) in fixed.items():
        recs[i]["printed"] = n
    with p.open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    have2 = sum(1 for r in recs if r.get("printed"))
    print(f"\n已写回 {p}")
    print(f"  printed {have} → {have2} 页（{have2/len(recs):.0%}）")
    print("  ★ 下一步跑 refolio.py 重新生成 txt，页码会一次到位")


if __name__ == "__main__":
    main()

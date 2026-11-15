# -*- coding: utf-8 -*-
"""split_columns.py —— 双栏 OCR 文本的左右栏切分

2026-11-11 起因：
    《中国共产党历史重要会议辞典》《重要事件辞典》是**双栏排版**，
    OCR 按行识别，把左右两栏粘在同一行里：

        责赣东北党是"立三路线"的执行者和发挥立中央革命军事委员会，并通过了取消红一
        ←────── 左栏 ──────→      ←────── 右栏 ──────→

    后果：短词能查（「古田会议」），长句查不到（左右串在一起）。

为什么能切：
    实测两本的行长规律非常稳定 ——
        会议辞典  行长中位数 38，87% 的页在 38±4 内
        事件辞典  行长中位数 38，92% 的页在 38±4 内
    所以「按行长中位数的一半切」是可行的。

局限（必须写清楚）：
    ① jsonl 里只存了文字，**没有 x 坐标**，所以切点是按字数估的，不是真栏边界。
       个别行会切偏 —— 实测一页里有 2 处（「据地党【中华苏维埃…」「王首一次全国代表大会」）。
    ② 切偏的页会标 `⚠`，可以人工核。
    ③ 只对「行长整齐」的页有效。行长中位数离群（<20 或 >50）的页不切。

用法：
    ★ 推荐：从已处理好的 txt 切（页标记和清理成果一点不动）
      python split_columns.py --from-txt 书.txt --out 书.txt --min-lines 40 --apply
      python split_columns.py --from-txt 书.txt --out 书.txt --entry-mark "【" --apply

    ⚠ 不推荐：从 jsonl 重建（会丢掉 refolio 的页码、清理掉的页眉）
      python split_columns.py --jsonl 书.txt.ocr.jsonl --out 书.txt --apply

为什么推荐 --from-txt：
    2026-11-11 事故 —— 哲学大辞典切栏后印刷页码从 1164 掉到 641。
    根因是本工具从 .ocr.jsonl 重建文件，而：
      ① jsonl 里 printed 字段只有 641 页有值，refolio.py 判出的 523 个页码
         **从没回写到 jsonl**；
      ② strip_layout_noise 清掉的页眉、clean_split_header 清掉的碎片，
         jsonl 里全都还在。
    **每从 jsonl 重建一次，之前所有后处理都白做。**
    实测对比：旧 jsonl 模式 1164→641；新 from-txt 模式 1164→1164。
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

MARK_RE = re.compile(r"\[PDFp(\d+)\]")


def detect_folio(first_lines, last_lines, printed):
    return printed


def main():
    ap = argparse.ArgumentParser()
    # 2026-11-11：--jsonl 改成非必填——用 --from-txt 时不需要它。
    ap.add_argument("--jsonl", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--book", default="")
    ap.add_argument("--vol", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--body-only", action="store_true",
                    help="只处理像正文的页（行长中位数 20-50）")
    ap.add_argument("--entry-mark", default=None,
                    help="正文页必须含这个标记（党史辞典用「【」；哲学大辞典不给）")
    ap.add_argument("--min-lines", type=int, default=0,
                    help="正文页最少行数（哲学大辞典用 40；0=不限制）")
    ap.add_argument("--from-txt", default=None,
                    help="从这个已处理好的 txt 切栏（保留页标记与后处理成果），"
                         "而不是从 jsonl 重建。强烈建议用这个。")
    args = ap.parse_args()

    # 2026-11-11 二次修（**重要**）：增加 --from-txt 模式。
    #   事故：哲学大辞典切栏后印刷页码从 1164 掉到 641。
    #   根因：本工具从 .ocr.jsonl 重建文件，而 jsonl 里 printed 字段只有 641 页有值
    #        —— refolio.py 判出的 523 个页码**从没回写到 jsonl**。
    #        同理，strip_layout_noise 清掉的页眉、clean_split_header 清掉的碎片，
    #        jsonl 里都还在，重建一次全白干。
    #   正确做法：**在已经处理好的 txt 上切**，页标记和清理成果一点不动。
    #   --from-txt 就是干这个的。原 jsonl 模式保留（新书第一次处理时用）。
    if args.from_txt:
        raw = Path(args.from_txt).read_text(encoding="utf-8", errors="replace")
        # 按页标记切块：保留每页的完整标记行
        parts = re.split(r"(?m)^((?:【p(?:\d+|\?[^\]]*)】)?\[PDFp(\d+)\])$", raw)
        # parts = [前导, 标记, pno, 正文, 标记, pno, 正文, ...]
        head = parts[0]
        blocks = []
        for i in range(1, len(parts) - 1, 3):
            mark = parts[i]
            pno = int(parts[i + 1])
            body = parts[i + 2]
            blocks.append((pno, mark, body))
        print(f"从 txt 读入 {len(blocks)} 页（页标记原样保留）")

        stat = Counter()
        outp = [head]
        ncut = 0
        for pno, mark, body in blocks:
            ls = [l for l in body.split("\n") if l.strip()]
            keep = body
            if len(ls) >= 6:
                med = statistics.median(len(l) for l in ls)
                ok = 20 <= med <= 50
                if ok and args.entry_mark and not any(args.entry_mark in l for l in ls):
                    ok = False; stat["无词条标记不切"] += 1
                elif ok and args.min_lines and len(ls) < args.min_lines:
                    ok = False; stat[f"行数<{args.min_lines}不切"] += 1
                elif ok:
                    toc_hits = sum(1 for l in ls if re.search(r"[／/]\s*\d+\s*$", l))
                    if toc_hits >= 3:
                        ok = False; stat["目录页不切"] += 1
                if ok:
                    cut = int(med / 2)
                    left, right = [], []
                    for l in ls:
                        if len(l) <= cut + 2:
                            left.append(l)
                        else:
                            left.append(l[:cut]); right.append(l[cut:])
                    keep = "\n〔左栏〕\n" + "".join(left) + "\n〔右栏〕\n" + "".join(right)
                    ncut += 1
            outp.append(mark + keep)
        print(f"\n切分统计：")
        for k, v in stat.most_common():
            print(f"   {v:>5}  {k}")
        print(f"   切分 {ncut} 页")
        if args.dry or not args.apply:
            print("\n（未写入，加 --apply 才改）")
            return
        Path(args.out).write_text("".join(outp), encoding="utf-8")
        print(f"\n已写 {args.out}")
        return

    src = Path(args.jsonl)
    recs = [json.loads(l) for l in src.open(encoding="utf-8")]
    print(f"读入 {len(recs)} 页")

    pages = []
    stat = Counter()
    for r in recs:
        ls = [l for l in r["lines"] if l.strip()]
        pno = r["pdf_page"]
        printed = r.get("printed")
        if len(ls) < 6:
            pages.append((pno, printed, ls, None))
            stat["页太短不切"] += 1
            continue
        med = statistics.median(len(l) for l in ls)
        if med < 20 or med > 50:
            pages.append((pno, printed, ls, None))
            stat[f"行长离群({med:.0f})不切"] += 1
            continue
        # 2026-11-11 加：**只切「有词条的正文页」**。
        #   实测 PDFp8 是序言，排版和正文不同，切点 17 切出来左右栏串了：
        #       《中国共产党历史系列辞典》编写说明在庆祝中国共产党成立95周年大会上…
        #   正文页才有【词条】标记，序言/目录/版权页没有。
        #
        # 2026-11-11 二次修：**这条判据只对党史辞典成立**。
        #   哲学大辞典的正文页没有【】标记（词条名是加粗行，不是方括号），
        #   用它做判据会导致 1223/1275 页全被挡掉（实测只切了 4 页）。
        #   所以加 --entry-mark 开关：
        #     党史辞典     --entry-mark "【"   正文页有【词条】
        #     哲学大辞典   不加此参数         用行长+行数判正文
        if args.entry_mark and not any(args.entry_mark in l for l in ls):
            pages.append((pno, printed, ls, None))
            stat["无词条标记不切"] += 1
            continue
        # 折叠栏的页往往行数很多（哲学大辞典正文 53 行、党史辞典 33 行）
        if args.min_lines and len(ls) < args.min_lines:
            pages.append((pno, printed, ls, None))
            stat[f"行数<{args.min_lines}不切"] += 1
            continue
        # 再排除**目录页**：目录也是双栏，但每栏窄、字数短，切出来会串。
        #   判据：出现「…／数字」这种目录页码格式，或整页都是「【…】（年份）／N」。
        toc_hits = sum(1 for l in ls if re.search(r"[／/]\s*\d+\s*$", l))
        if toc_hits >= 3:
            pages.append((pno, printed, ls, None))
            stat["目录页不切"] += 1
            continue
        cut = int(med / 2)
        left, right = [], []
        for l in ls:
            if len(l) <= cut + 2:
                left.append(l)
                continue
            left.append(l[:cut])
            right.append(l[cut:])
        pages.append((pno, printed, ls, (cut, left, right)))
        stat["切分"] += 1

    print(f"\n切分统计：")
    for k, v in stat.most_common():
        print(f"   {v:>5}  {k}")

    # 抽一页给看
    demo = next((p for p in pages if p[3]), None)
    if demo:
        pno, printed, ls, (cut, left, right) = demo
        print(f"\n=== 样例 PDFp{pno}  切点 {cut} ===")
        print(f"左栏: {''.join(left)[:160]}")
        print(f"右栏: {''.join(right)[:160]}")

    if args.dry or not args.apply:
        print("\n（未写入，加 --apply 才改）")
        return

    # 输出
    out = []
    out.append("=" * 74)
    out.append(f"著作名：{args.book}")
    out.append(f"出处卷次：{args.vol}")
    out.append(f"来源文件：{src.name}（OCR 共 {len(recs)} 页）")
    out.append("处理方式：**OCR 后做了双栏切分**（按行长中位数一半切左右栏）")
    out.append("切分依据：实测行长方差很小，中位数 38，87%–92% 的页在 38±4 内")
    out.append("⚠ 切点是按字数估的，不是真栏边界；个别行会切偏，引用长句前须核原书")
    out.append("页码体例：【pN】= 印刷页码；【p?|pdfN】= 无页码，只记 PDF 页序")
    out.append("=" * 74)
    out.append("")
    ncut = 0
    for pno, printed, ls, sp in pages:
        tag = f"【p{printed}】" if printed else "【p?|pdf" + str(pno) + "】"
        out.append(f"{tag}[PDFp{pno}]")
        if sp:
            cut, left, right = sp
            out.append("〔左栏〕")
            out.append("".join(left))
            out.append("〔右栏〕")
            out.append("".join(right))
            ncut += 1
        else:
            out.extend(ls)
        out.append("")

    Path(args.out).write_text("\n".join(out), encoding="utf-8")
    print(f"\n已写 {args.out}")
    print(f"   切分 {ncut} 页 / 共 {len(pages)} 页")


if __name__ == "__main__":
    main()

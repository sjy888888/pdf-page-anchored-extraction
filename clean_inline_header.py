# -*- coding: utf-8 -*-
"""clean_inline_header.py —— 清「印在版心内的页码 + 书名眉」

2026-11-11 起因（拼接连着踩三次同一处）：
    44卷的页面结构是这样：

        应该而且可以向其他国家学习。一个社会即使探索到了本身运动的
        （空行）
        【p10】[PDFp23]
        10                      ← 印在版心内的页码
        第一版序言              ← 书名眉
        自然规律，—             ← 正文续行
        本书的最终目的就是揭示现代社会的经济运动规律，

    join_page_breaks 找「下一页首行」时取到的是 `10`，不是 `自然规律，—`，
    于是拼成「…本身运动的10」，完整句子永久断裂。
    实测全库 2,027 处这种结构，44卷一本就有 577 处。

    这批页码/书名眉 L1（页码清理）、L2（页眉清理）都抓不到——
    因为页码不是「独立成行」而是紧接着页标记、页眉也不是「整页首行重复」
    而是夹在版心里的片段。

判据（三条同时成立才删）：
    ① 该行紧接在页标记之后（允许一个空行）
    ② 该行是**纯数字**，且等于这一页的印刷页码（页标记里有）
       或者：该行是**短中文行**（2–24 字），且它和上面那个数字一起出现
    ③ 删掉它之后，页标记后面第一行是正常长度的正文

处置：
    只删「数字行」，和「数字行之后那一行短中文」（书名眉）。
    全记清单，可回滚。

用法：
    python clean_inline_header.py --layer L0-马恩列原著 --dry
    python clean_inline_header.py --layer L0-马恩列原著 --apply
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path(r"G:\path\to\knowledge-base\学科资料")
LOG_DIR = Path(r"G:\path\to\knowledge-base\_修正清单")
MARK = re.compile(r"^(?:【p(\d+)[※]?】)?\[PDFp(\d+)\]$")

SENT = "。！？…．.!?」』）】”’\"'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", default=None)
    ap.add_argument("--book", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--max-name", type=int, default=24, help="书名眉最大长度")
    args = ap.parse_args()

    if args.book:
        files = [f for f in BASE.rglob("*.txt") if args.book in f.name
                 and not any(x.startswith("_") for x in f.relative_to(BASE).parts if x)]
    elif args.layer:
        files = [f for f in (BASE / args.layer).rglob("*.txt")
                 if not any(x.startswith("_") for x in f.relative_to(BASE).parts if x)]
    else:
        files = [f for f in BASE.rglob("*.txt")
                 if not any(x.startswith("_") for x in f.relative_to(BASE).parts if x)]

    total = 0
    per_file = []
    samples = []
    changed = []

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = raw.split("\n")
        marks = [(i, MARK.match(l.strip())) for i, l in enumerate(lines) if MARK.match(l.strip())]
        if len(marks) < 10:
            continue

        drop = set()
        n = 0
        for idx, (pos, m) in enumerate(marks):
            printed = m.group(1)
            nxt = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
            # 收集页标记之后的前 3 个非空行
            body = []
            for j in range(pos + 1, min(nxt, pos + 8)):
                if lines[j].strip():
                    body.append((j, lines[j].strip()))
                if len(body) >= 3:
                    break
            if len(body) < 2:
                continue
            j0, s0 = body[0]
            j1, s1 = body[1]

            # ① 第一行必须是纯数字
            if not s0.isdigit():
                continue
            # ② 且等于本页印刷页码（若有）
            if printed and s0 != printed:
                # 页标记里的页码和版心页码不一致 → 可能是别的东西，不删
                continue
            # ③ 第二行是短书名眉
            name_ok = (2 <= len(s1) <= args.max_name
                       and not s1.isdigit()
                       and not s1.startswith("〔")
                       and re.search(r"[\u4e00-\u9fff]", s1))
            if not name_ok:
                continue
            # ④ 删完之后，下一个非空行必须是像正文的行（防止把正文删了）
            #   2026-11-11 放松：原来要求 ≥12 字，但实测 44卷 p10 那处
            #   续行是「自然规律，—」（5 字），正好是**跨页断句的后半截**，
            #   12 字门槛把它挡了，结果 `10`+`第一版序言` 留在原地，
            #   join_page_breaks 又把 `10` 当成下一页首行接上去。
            #   改成 ≥4 字，且不能是纯数字。
            body2 = []
            for j in range(j1 + 1, min(nxt, j1 + 10)):
                if lines[j].strip():
                    body2.append(lines[j].strip())
                if len(body2) >= 1:
                    break
            if not body2:
                continue
            after = body2[0]
            if len(after) < 4 or after.isdigit():
                continue

            drop.add(j0)
            drop.add(j1)
            n += 2
            if len(samples) < 8:
                samples.append((f.name, printed or "", s0, s1, after[:30]))

        if not drop:
            continue
        total += n
        per_file.append((str(f.relative_to(BASE)), n))
        if args.apply and not args.dry:
            newlines = [l for i, l in enumerate(lines) if i not in drop]
            changed.append((f, "\n".join(newlines)))

    print(f"处理 {len(files)} 文件")
    print(f"  删除版心页码+书名眉 {total} 行，涉及 {len(per_file)} 文件\n")

    print("=== 样例（页码 / 书名眉 / 删后接上的正文）===")
    for fn, printed, s0, s1, after in samples:
        print(f"  {fn[:26]:<28} p{printed:<4} 数字「{s0}」 书名眉「{s1}」")
        print(f"  {'':<28} → 接着的正文: {after}…")
        print()

    if args.dry or not args.apply:
        print("（未写入，加 --apply 才改）")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logp = LOG_DIR / f"inline-header-{stamp}.json"
    logp.write_text(json.dumps({
        "time": stamp, "removed": total,
        "per_file": [{"path": p, "n": n} for p, n in per_file],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    for f, new in changed:
        f.write_text(new, encoding="utf-8")
    print(f"已写回 {len(changed)} 个文件")
    print(f"清单：{logp}")


if __name__ == "__main__":
    main()

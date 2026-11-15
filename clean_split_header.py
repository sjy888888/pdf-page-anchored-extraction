# -*- coding: utf-8 -*-
"""clean_split_header.py —— 清掉「被页标记劈开的页眉」

2026-11-11 起因（文集07 实测）：
    页眉「序言」被 OCR 切成两半，一半粘在上一页末尾，一半在下一页开头：

        这一篇…可以按照其轮廓来加以序      ← 「序」粘在页尾
        【p9】[PDFp27]
        兰F                                 ← 碎片
        F司                                 ← 碎片

        究。他对于俄国1861年"改革"4 以后…所有权的序     ← 「序」粘在页尾
        【p11】[PDFp29]
        言                                              ← 「言」在页首

    这种结构拼接工具处理不了：它看到的是「上一行末尾是『序』，下一行是『言』」，
    会拼成「序言」——但那就把页眉接到正文里去了。

    真正该做的是：**把这两半页眉都删掉**，让正文恢复到「…所有权的」+「统计资料…」。

判据（三条一起用，宁可不删）：
    ① 页标记前一行，末尾 1-2 字属于「页眉词尾」集合（序/言/录/注/释/记/跋/例）
    ② 页标记后第一行，长度 ≤2 且是这个页眉词的剩余部分
       （或者：后一行本身就是极短碎片，如 `兰F`、`F司`）
    ③ 删掉后，前一行末尾和后一行开头能接上更通顺

处置：
    删掉前一行的末尾 1-2 字 + 删掉后一行的碎片行。
    全记入清单，可回滚。

用法：
    python clean_split_header.py --layer L0-马恩列原著 --dry
    python clean_split_header.py --layer L0-马恩列原著 --apply
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
MARK = re.compile(r"^(?:【p\d+[※]?】)?\[PDFp(\d+)\]$")

# 页眉词的组成字（页眉常是这些词的某一段）
HEADER_CHARS = set("序言凡例目录附录注释后记跋导前")
# 页标记后「碎片行」的特征：极短、非数字、非正常词
FRAG_MAXLEN = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", default=None)
    ap.add_argument("--book", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
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

    total_trim = total_drop = 0
    per_file = []
    samples = []
    changed = []

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = raw.split("\n")
        marks = [i for i, l in enumerate(lines) if MARK.match(l.strip())]
        if len(marks) < 10:
            continue
        freq = Counter(l.strip() for l in lines if l.strip())

        trim = {}     # 行号 -> 要删掉的末尾字符数
        drop = set()  # 要整行删掉的
        n_t = n_d = 0

        for pos in marks:
            # 前一行 / 后一行
            pi = None
            for j in range(pos - 1, max(-1, pos - 4), -1):
                if j >= 0 and lines[j].strip():
                    pi = j
                    break
            ni = None
            for j in range(pos + 1, min(len(lines), pos + 4)):
                if lines[j].strip():
                    ni = j
                    break
            if pi is None or ni is None:
                continue
            a = lines[pi].strip()
            b = lines[ni].strip()
            if not a or not b:
                continue

            # ① 前一行末尾是页眉字
            if a[-1] not in HEADER_CHARS:
                continue
            # 前一行本身不能太短（太短可能是标题）
            if len(a) < 12:
                continue
            # 2026-11-11 加：**前一行末尾是完整词就不许砍**。
            #   实测坏样例：`…越南北部的旧称。一一编者注` + `释`
            #   砍掉「注」变成「编者」，把原文弄坏了。
            if re.search(r"(编者注|译者注|校者注|作者注|原注|脚注|尾注)$", a):
                continue
            # ② 后一行是小碎片
            if len(b) > FRAG_MAXLEN:
                continue
            # 后一行不能是正常词
            if b.isdigit():
                continue
            # 后一行的字必须也是页眉字（或是明显的 OCR 碎片）
            is_hdr_frag = all(c in HEADER_CHARS for c in b)
            is_noise = bool(re.fullmatch(r"[A-Za-z0-9\s]{1,3}", b)) or \
                       bool(re.fullmatch(r"[\u4e00-\u9fff]{1,2}", b)) and freq.get(b, 0) >= 3
            if not (is_hdr_frag or is_noise):
                continue

            # 通过 → 前一行去掉末尾 1-2 个页眉字，后一行删掉
            cut = 0
            while cut < len(a) and cut < 2 and a[-1 - cut] in HEADER_CHARS:
                cut += 1
            if cut == 0:
                continue
            trim[pi] = cut
            drop.add(ni)
            n_t += 1
            n_d += 1
            if len(samples) < 8:
                samples.append((f.name, a[-30:], b, a[:-cut][-30:]))

        if not trim:
            continue
        total_trim += n_t
        total_drop += n_d
        per_file.append((str(f.relative_to(BASE)), n_t))

        if args.apply and not args.dry:
            newlines = []
            for i, l in enumerate(lines):
                if i in drop:
                    continue
                if i in trim:
                    s = l.rstrip()
                    # 保留缩进结构：只砍末尾字符
                    newlines.append(s[:-trim[i]] if trim[i] <= len(s) else s)
                else:
                    newlines.append(l)
            changed.append((f, raw, "\n".join(newlines)))

    print(f"处理 {len(files)} 文件")
    print(f"  砍掉末尾页眉字 {total_trim} 处")
    print(f"  删掉碎片行     {total_drop} 行")
    print(f"  涉及 {len(per_file)} 文件\n")

    print("=== 样例（原 → 清理后）===")
    for fn, atail, b, after in samples:
        print(f"  {fn[:26]:<28} …{atail}")
        print(f"  {'':<28} 碎片行: {b!r}")
        print(f"  {'':<28} → …{after}")
        print()

    if args.dry or not args.apply:
        print("（未写入，加 --apply 才改）")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logp = LOG_DIR / f"split-header-{stamp}.json"
    logp.write_text(json.dumps({
        "time": stamp, "trim": total_trim, "drop": total_drop,
        "per_file": [{"path": p, "n": n} for p, n in per_file],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    for f, orig, new in changed:
        f.write_text(new, encoding="utf-8")
    print(f"已写回 {len(changed)} 个文件")
    print(f"清单：{logp}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""strip_layout_noise.py —— 清掉抽取器带进来的版式垃圾

2026-11-11 背景：
    pdf2txt_paged.py 第 341-347 行把 page.get_text("text") 整页倾倒，
    页眉、页脚、印刷页码统统进正文，且**页眉会把跨页的句子劈成两半**：

        独立的形式，如利润…剩余价值的这些转
        【p652】[PDFp665]
        652
        第七篇资本的积累过程
        化形式在第三册180里才能研究。

    「转化形式」被切成「转 / 化形式」。检索跨页句子直接断。

三档判据（从严到宽，默认只跑第一档）：
    L1 印刷页码独立成行   纯数字 且 值 == 本页【pN】
                          —— 判据最硬，几乎不可能误伤
    L2 版式页眉/页脚      在某页正文最前 3 行或最后 2 行
                          且该串在全书出现 ≥N 次（N 按页数自适应）
                          —— 需人工过一眼
    L3 已知整行水印       S22PDF、MEGA 卷标、出版社行等固定串

用法：
    python strip_layout_noise.py --level 1 --dry
    python strip_layout_noise.py --level 1 --apply
    python strip_layout_noise.py --level 2 --book "文集05" --dry
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
MARK = re.compile(r"^(?:【p(\d+)】)?\[PDFp(\d+)\]$")
PLACE = "〔本页无可抽取文字层"

# L3：已知整行水印，直接删
# 2026-11-11 收窄（v1 太宽，会误删真标题）：
#   v1 里 RE_MEGA_LABEL 匹配 `目录`/`Inhalt`/`Text` —— 这些可能是真小标题，
#   实测 MEGA2-III-6 的 Inhalt/Text/Apparat 是卷内结构标，不是页眉。
#   现在只保留**确定是印刷版式、绝不可能是正文**的那几种：
#     · S22PDF 版权水印（pdf@home.icm.ac.cn 那条）
#     · 单独一行的出版社 + 年份（Berlin 2003 / Dietz Verlag 之类）
#     · 单独的 MEGA 卷标（MEGA II/14）
RE_S22 = re.compile(r"^本PDF文件由S22PDF生成.*$")

# 2026-11-11：**结构标记白名单**。清理时一律跳过。
#   只保护「删了会导致篇目提不出来」的那几个：
#     build_toc.py 靠 DIR_RE = ^\s*目\s*录\s*$ 找目录页，删了篇目就全丢。
#   实测教训：
#     ① 马恩全集 11/16/44 卷的「目录」被当页眉删掉 → 重建 _toc 丢 46 本，只能回滚
#     ② 反过来也踩过：「人名索引」「文献索引」「注」这类**每页重复出现**，
#        是页眉不是结构标记，不该保护（实测 44 卷「人名索引」21 页、
#        「文献索引」36 页、「注」29 页，后面都紧跟正文）。
#   所以白名单要窄：只放真正的一次性结构标记。
STRUCT_KEEP = re.compile(
    r"^\s*(目\s*录|目\s*次|总\s*目\s*录|凡\s*例|编\s*辑\s*说\s*明|出\s*版\s*说\s*明)\s*$")
RE_PUB_LINE = re.compile(r"^(?:Berlin|Dietz|Akademie|Karl\s*Dietz)\s*(?:Verlag)?\s*\d{4}$")
RE_MEGA_LABEL = re.compile(r"^MEGA\s*(?:I{1,3}|\d+)\s*[/.]\s*\d+$")


def parse_pages(lines):
    """切成 (页标记行号, 印刷页码 or None, PDF页码, 正文行[(行号, 内容)])"""
    marks = []
    for i, l in enumerate(lines):
        m = MARK.match(l.strip())
        if m:
            marks.append((i, m.group(1), int(m.group(2))))
    if len(marks) < 5:
        return []
    segs = []
    for k, (pos, printed, pno) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        body = [(j, lines[j].strip()) for j in range(pos + 1, end) if lines[j].strip()]
        segs.append((pos, printed, pno, body))
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=1, choices=[1, 2, 3])
    ap.add_argument("--book", default=None)
    ap.add_argument("--layer", default=None, help="只处理某一层，如 L1-中国化马克思主义")
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

    total = 0
    per_file = []
    changed = []
    samples = []

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not re.search(r"(?m)^\[PDFp\d+\]|^【p", raw):
            continue
        lines = raw.split("\n")
        segs = parse_pages(lines)
        if len(segs) < 5:
            continue

        drop = set()     # 要删的行号（0-based）

        # ── L1：印刷页码独立成行 ──
        if args.level >= 1:
            for pos, printed, pno, body in segs:
                if not printed:
                    continue
                for j, s in body:
                    if s.isdigit() and s == printed:
                        drop.add(j)

        # ── L2：版式页眉 ──
        # 2026-11-11 判据演化（实测四版）：
        #   v1「前3行+后2行 且 全书≥10次」→ 漏掉 `第七篇资本的积累过程`
        #      阈值 thr=max(10,页数*0.10)=110 太高。
        #   v2「只取每页第一行 且 全书≥10次 且 ≤30字符」→ 第44卷命中 907 行，
        #      查了命中清单，几乎全是真页眉，误伤 0。
        #   v3 加「1% 页数」阈值 → 第44卷 897 行。
        #   v4（本版）补「标题形状」判据：
        #      实测发现 `第二版跋`（10次）、`第一版序言`、`注释` 这类篇名做页眉，
        #      但次数不够 1% 阈值，漏掉后会在跨页拼接时接出
        #      「…可是我没有 + 第二版跋…」这种垃圾。
        #      所以再加一条：**看起来像标题的行，且在全书中只出现在页首**，
        #      就算次数不够也删。
        if args.level >= 2:
            npages = len(segs)
            first_freq = Counter()
            firsts = []
            first_line_set = set()      # (pos, 行号) 标记「本页正文第 1 行」
            # 2026-11-11：强制删除集（页眉+页码粘行的变体，走强删，
            #   不吃「保留第 1 次」那套保护——它们每行只出现 1 次，
            #   保护逻辑会让它们一页都删不掉）。
            strong_drop = set()
            for pos, printed, pno, body in segs:
                if not body:
                    continue
                f0 = body[0][1]
                first_freq[f0] += 1
                firsts.append((pos, body[0][0], f0))
                first_line_set.add((pos, body[0][0]))
            thr = max(8, int(npages * 0.01))
            _seen = {}       # 记录同一串已经遇到几次，第 1 次保留

            # 全书任意位置出现过该行的页数（用于判「只在页首出现」）
            anywhere = Counter()
            for pos, printed, pno, body in segs:
                for _, s in body:
                    anywhere[s] += 1

            # 标题形状：第X章/节/版序言/版跋/注释/索引/一、二、…
            TITLE_SHAPE = re.compile(
                r"^(第[一二三四五六七八九十百零〇\d]+[章节篇部卷篇]|"
                r"第[一二三四五六七八九十\d]+版(?:序言|跋)|"
                r"[一二三四五六七八九十]+、|"
                r"注\s*释$|.*索引$|.*译名.*$|插图$|目录$|前言$|后记$|"
                # 2026-11-11 补：实测 L0 里「序言」「凡例」「序」「前 言」做成页眉，
                #   全书只有 4-7 次，达不到 1% 阈值（文集07 阈值 12），
                #   于是漏掉，页眉跑到上一行末尾，拼接时接成「马序克思」。
                r"序\s*言$|序$|凡\s*例$|前\s*言$|后\s*记$|附\s*录$|"
                r"出版说明$|编辑说明$|内容提要$|作者简介$)")

            for pos, j, s in firsts:
                if len(s) > 30:
                    continue
                if s.startswith("〔本页无可抽取"):     # 抽取器自家占位，绝不删
                    continue
                # 2026-11-11：结构标记（目录/索引/插图/凡例…）永不删。
                #   详见文件头 STRUCT_KEEP 的注释。
                if STRUCT_KEEP.match(s):
                    continue
                # 这一行是不是「本页正文第 1 行」
                is_first_line = (pos, j) in first_line_set
                hit = False
                if first_freq[s] >= thr:
                    hit = True
                elif TITLE_SHAPE.match(s) and first_freq[s] >= 2 and anywhere[s] <= first_freq[s] * 1.2:
                    # 像标题 + 几乎只在页首出现 + 至少 2 次
                    hit = True
                # 2026-11-11 补：**页首的极短行**（≤4 字）几乎一定是页眉。
                #   实测：「序」「凡 例」「前 言」这类，全书出现 4-7 次，
                #   达不到 1% 阈值（文集07 有 1210 页 → 阈值 12），于是漏掉，
                #   结果页眉粘到上一行末尾，拼接时接成「马序克思」「凡例1.正文」。
                #   判据：在页首 + ≤4 字 + 至少出现 3 次 + 不是纯数字（页码另有处理）。
                elif len(s) <= 4 and is_first_line and first_freq[s] >= 3 \
                        and not s.isdigit():
                    hit = True
                # 2026-11-11 补：**页眉+页码粘成一行的变体**。
                #   实测《马克思主义哲学大辞典》每页首行是这样：
                #       2马克思主义哲学理论来源
                #       马克思主义哲学理论来源3
                #       4马克思主义哲学理论来源
                #       14乌克思主义哲学理论来源     ← OCR 还把「马」认成「乌」
                #   页码位置忽前忽后，且每页页码不同，所以 freq 统计永远命中不了。
                #   判据：去掉所有数字后，这一行和全书其它页首行**高度相似**。
                #   注意：不依赖 first_line_set（那个集合的索引口径和这里对不上，
                #   实测同一个 s 在集合里查不到）。
                if not hit and len(s) <= 30:
                    stripped = re.sub(r"\d+", "", s)
                    if len(stripped) >= 6:
                        same = 0
                        for s2, n2 in first_freq.items():
                            s2c = re.sub(r"\d+", "", s2)
                            if len(s2c) >= 6 and s2c[:8] == stripped[:8]:
                                same += n2
                        if same >= max(8, int(npages * 0.05)):
                            hit = True
                            strong_drop.add(j)
                if hit:
                    # ★ 2026-11-11 修正两次：
                    #   第一版：全删 → 280 个真篇名（哥达纲领批判/《法兰西内战》初稿）整个消失
                    #   第二版：只保留第 1 次 → 但「第1次」可能出现在某页的**第 2 行**
                    #           （正文里的一次引用），结果真正页首那次反倒被删了。
                    #           实测：文集05「资本主义积累的一般规律」32 次，28 次在页首，
                    #           被保留的却是 L20392（某页第 2 行）。
                    #   现在：**只在页首出现的，保留最早那一次页首；非页首的一律不碰。**
                    #        这样篇名留在它该在的位置，页眉全清。
                    if j in strong_drop:
                        drop.add(j)
                        continue
                    k = _seen.get(s, 0)
                    _seen[s] = k + 1
                    if k >= 1 or not is_first_line:
                        # 页首的第 1 次留着；第 2 次起删
                        # 但如果这一行不在页首（正文里的引用），不删
                        if is_first_line:
                            drop.add(j)

        # ── L3：固定水印 ──
        if args.level >= 3:
            for j, l in enumerate(lines):
                s = l.strip()
                if RE_S22.match(s) or RE_MEGA_LABEL.match(s) or RE_PUB_LINE.match(s):
                    drop.add(j)

        if not drop:
            continue

        total += len(drop)
        per_file.append((str(f.relative_to(BASE)), len(drop), len(segs)))
        if len(samples) < 6:
            for j in sorted(drop)[:2]:
                samples.append((f.name, j + 1, lines[j][:56]))

        if not args.dry and args.apply:
            kept = [l for i, l in enumerate(lines) if i not in drop]
            changed.append((f, raw, "\n".join(kept)))

    print(f"L{args.level}  扫描 {len(files)} 文件")
    print(f"命中 {len(per_file)} 文件，共 {total:,} 行\n")
    print(f"{'行数':>6} {'页数':>6}  文件")
    print("-" * 92)
    for rel, n, np_ in sorted(per_file, key=lambda x: -x[1])[:20]:
        print(f"{n:>6} {np_:>6}  {rel[-76:]}")

    print(f"\n样例：")
    for name, ln, txt in samples:
        print(f"  {name[:30]:<32} L{ln}: {txt}")

    if args.dry or not args.apply:
        print("\n（未写入，加 --apply 才改）")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logp = LOG_DIR / f"strip-L{args.level}-{stamp}.json"
    logp.write_text(json.dumps({
        "time": stamp, "level": args.level, "total": total,
        "per_file": [{"path": p, "dropped": n, "pages": pg} for p, n, pg in per_file],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    for f, orig, new in changed:
        f.write_text(new, encoding="utf-8")
    print(f"\n已写回 {len(changed)} 个文件")
    print(f"清单：{logp}")


if __name__ == "__main__":
    main()

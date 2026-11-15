# -*- coding: utf-8 -*-
"""join_page_breaks.py —— 跨页断句拼接

2026-11-11 背景：
    抽取器把每页当独立块输出，页与页之间是：
        上一页末行
        ⟨空行⟩
        ⟨空行⟩
        【p652】[PDFp665]
        下一页首行
    于是跨页的句子被劈开：
        「…剩余价值的这些转」
        【p652】
        「化形式在第三册180里才能研究。」
    检索「剩余价值的这些转化形式」永远搜不到。

判据（三道，缺一不可，宁可不接也不许接错）：
    1. 上一页最后一行**不以句末标点结尾**
       —— 句末标点 = 。！？…」』）】"' 以及英文 . ! ?
       理由：如果已经句末，那本来就是一页结束，不该接。
    2. 上一页末行长度 ≥ 15 字
       —— 太短的行可能是标题/短句，接上去风险高
    3. 下一页首行不是标题特征（不以「第X章/第X节/一、二、」开头，
       不以书名号单独成行）
    4. 拼接处两行都必须是纯正文（不含〔占位〕等）

接法：把上一页末行和下一页首行合并成一行，中间不加空格
      （中文排版，行内断行本来就没有空格）。
      页标记行保留原位不动 —— 页码溯源不能丢。

用法：
    python join_page_breaks.py --book "马恩全集2版-第44卷" --dry
    python join_page_breaks.py --apply
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
# 2026-11-11：**拼接幂等标记**。
#   实测问题：工具连跑两次会继续拼更早的断点（文集07 第二次又改 367 处），
#   不收敛。虽然 PDF 核对证明没编造内容（24/30 页通过），但每次跑都在累积风险。
#   做法：拼完后在文件头部写一行标记，第二次跑时看到就跳过。
JOIN_MARK = "本件已做跨页断句拼接"

# 句末标点（含中英文、各种引号收尾）
SENT_END = "。！？…．.!?」』）】”’\"'"
# 目录行特征（2026-11-11 实测：不加这条会把目录页接出一堆垃圾）
#   实测坏样例：「..................481」+「巨」、「............... 607-611」+「6」
TOC_LINE = re.compile(r"(?:\.{3,}|…{2,}|·{3,}|\.\s*\d+\s*$|\d+\s*$)")
# 2026-11-11 新增两道闸（抽验时发现会拼错的两类）：
#
#   闸 A：页眉夹在中间
#     实测坏样例：`…集中主要力` + [页眉「江泽民文选　第一卷」] + `量发展微电子工业`
#     拼完变成「力江泽民文选　第一卷量发展」——把页眉当正文接进去了。
#     判据：上一行末尾或下一行开头出现**书名+卷次**这种版式串，就不拼。
HEADER_INLINE = re.compile(r"^[^。，；：！？]{0,20}(?:文选|文集|全集|选集|著作集)\s*[（(]?第?\s*[一二三四五六七八九十\d]+\s*[卷册][）)]?\s*$")
#
#   闸 B：外文书名/术语断行
#     实测坏样例：`…表达的思Das KapitaL` + `Kritik der politischen Oekonomie`
#     中外文混排时，断点落在词中间，拼上去是错的。
#     判据：上一行末尾 12 字内含 ≥4 个连续拉丁字母，或下一行开头是纯外文。
LATIN_TAIL = re.compile(r"[A-Za-z]{4,}")
LATIN_HEAD = re.compile(r"^[A-Za-z][A-Za-z\s,.:;()\[\]'\-—]{6,}$")

# 标题特征：不能当正文首行接上去
# 2026-11-11 补：OCR 会把「第一版序言」读成「第--版序言」，
#   「一」被认成两个连字符。所以数字位要允许 -- / ~ / — 。
TITLE_HEAD = re.compile(r"^(第[一二三四五六七八九十百零〇\d\-—~]{1,4}[章节篇部卷版]|"
                        r"[一二三四五六七八九十]+、|"
                        r"[（(][一二三四五六七八九十\d]+[)）]|"
                        r"〔本页无可抽取|注\s*释$|人名索引$|名目索引$|文献索引$|"
                        r"报刊索引$|译名对照$|插图$|目录$|前言$|后记$|"
                        r"第[\-—~]+版(?:序言|跋))")
PLACE = "〔本页无可抽取"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None)
    ap.add_argument("--layer", default=None, help="只处理某一层，如 L0-马恩列原著")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--min-len", type=int, default=15,
                    help="上一页末行最短长度（太短不接）")
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

    total_joined = 0
    total_skip = 0
    per_file = []
    already = []        # 2026-11-11：已拼过（有 JOIN_MARK）而跳过的文件
    changed = []
    samples = []
    skip_reasons = {"末行已句末": 0, "末行太短": 0, "首行像标题": 0, "占位行": 0,
                    "空页": 0, "目录行": 0, "编者注尾": 0,
                    "页眉夹中间": 0, "外文断行": 0, "上行为页眉": 0, "下行太短": 0,
                    "上行是篇名": 0, "表格页": 0, "已拼过": 0, "下行是书眉": 0,
                    "下行是页码页眉": 0, "下行为页眉": 0, "下行为页码页眉": 0}

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not re.search(r"(?m)^\[PDFp\d+\]|^【p", raw):
            continue
        # 2026-11-11：**幂等保护**。头部区已有标记 = 拼过了，跳过。
        #   实测：连跑两次会继续拼更早的断点（文集07 第二次改 367 处），不收敛。
        if JOIN_MARK in raw[:3000]:
            already.append(f.name)
            continue
        lines = raw.split("\n")
        marks = [(i, MARK.match(l.strip())) for i, l in enumerate(lines) if MARK.match(l.strip())]
        if len(marks) < 10:
            continue

        # 2026-11-11：先算全书行频。用来判「这一行是不是页眉」。
        #   页眉的特征就是「同一行在全书出现很多次」。
        #
        # 2026-11-12 修（衣俊卿《20世纪新马克思主义》实测）：
        #   **统计前必须剥掉行尾标点**，否则同一页眉的变体会被拆散。
        #   实测：
        #       '引论20世纪马克思主义的分化及其基本格局'      5 次
        #       '引论20世纪马克思主义的分化及其基本格局：'     5 次
        #       '引论20世纪马克思主义的分化及其基本格局：：'    1 次
        #   每个变体都卡在 freq>=5 的门槛上（刚好不触发），
        #   于是「…对自己所属的共产」+「引论20世纪马克思主义的分化及其基本格局：」
        #   被拼成一句，页眉混进正文。
        #   剥标点后合计 11 次 → 稳稳触发。
        PUNCT_TAIL = "：:，,。.；;、！!？?　 \t"
        line_freq = Counter(l.strip().rstrip(PUNCT_TAIL) for l in lines if l.strip())

        # 2026-11-11 新增：**标记表格页**。
        #   表格页的特征：短行（≤6 字）占比 > 60%。
        #   实测文集07 有 41/1210 页是表格页（级差地租的表格），
        #   这类页的每一行是一个单元格，拼接会把「A」「4」「3镑」连成句子，弄坏表格。
        #   判据：只在这类页的**边界**不拼，页内不动。
        table_page = set()
        for pos, _m in marks:
            k2 = [x for x, _ in marks].index(pos)
            nxt2 = marks[k2 + 1][0] if k2 + 1 < len(marks) else len(lines)
            body = [l.strip() for l in lines[pos + 1:nxt2] if l.strip()]
            if len(body) >= 5:
                short = sum(1 for b in body if len(b) <= 6)
                if short / len(body) > 0.6:
                    table_page.add(pos)

        out = []
        prev = 0            # 已处理内容的末尾（下次从这里开始搬）
        last_mark = -1      # 上一个页标记的位置（往回找末行的下界）
        n_join = 0
        for k, (pos, m) in enumerate(marks):
            nxt = marks[k + 1][0] if k + 1 < len(marks) else len(lines)

            # 找上一页最后一个非空行：从 pos 往前找，下界是上一个页标记（不含）
            last_i = None
            for j in range(pos - 1, last_mark, -1):
                if j >= 0 and lines[j].strip():
                    last_i = j
                    break
            # 找下一页第一个非空行
            first_i = None
            for j in range(pos + 1, nxt):
                if lines[j].strip():
                    first_i = j
                    break

            can_join = False
            a = b = ""
            if last_i is None or first_i is None:
                skip_reasons["空页"] += 1
            elif last_mark in table_page or pos in table_page:
                # 2026-11-11：表格页边界不拼。详见上方 table_page 的注释。
                skip_reasons["表格页"] += 1
            else:
                a = lines[last_i].strip()
                b = lines[first_i].strip()
                # 2026-11-11 第十道闸（**最要紧的一道，防重复拼接**）：
                #   工具被跑第二次时，会把「已经合并过的上一行」再和下一行拼一次，
                #   结果是原文重复两遍。
                #   实测事故：列宁全集06 跑两次后出现
                #     L388: 发展的革命斗争的影响下…各                    ← 旧短版还在
                #     L389: 发展的革命斗争的影响下…各民粹派团体…社会革命党  ← 合并版
                #   判据：上一行已经**包含了下一行的开头**（≥8 字），
                #   说明这处拼过了，跳过。
                if len(b) >= 8 and b[:8] in a:
                    skip_reasons["已拼过"] += 1
                elif PLACE in a or PLACE in b:
                    skip_reasons["占位行"] += 1
                elif a[-1] in SENT_END:
                    skip_reasons["末行已句末"] += 1
                elif TOC_LINE.search(a) or TOC_LINE.search(b):
                    skip_reasons["目录行"] += 1
                elif HEADER_INLINE.match(b) or HEADER_INLINE.match(a):
                    skip_reasons["页眉夹中间"] += 1
                # 2026-11-11 第七道闸：**上一行整行是页眉**。
                #   实测坏样例：
                #     `…才谈得上「批注」的边` + [`约·狄慈根《短篇哲学著作集》一书批注`] + `史发展…`
                #     `…的大部分落到马` + [`序`] + `克思和我身上`
                #   这类上一行本身就是页眉（虽不含书名号，但全书高频重复），
                #   拼上去会把页眉接进正文。
                #   判据：上一行在全书出现 ≥5 次 → 它是页眉，不拼。
                # 2026-11-12：查表也要剥同样的标点（见 line_freq 构建处的说明）
                elif line_freq.get(a.rstrip("：:，,。.；;、！!？?　 \t"), 0) >= 5:
                    skip_reasons["上行为页眉"] += 1
                # 2026-11-12 第十三道闸：**下一行本身是页眉**。
                #   实测坏样例（衣俊卿《20世纪新马克思主义》）：
                #       a = '差异，甚至存在冲突和相互批判。这里既有屡遭磨难而对自己所属的共产'  （正文，freq=1）
                #       b = '引论20世纪马克思主义的分化及其基本格局：'                      （页眉，freq=11）
                #   拼完变成「…对自己所属的共产引论20世纪马克思主义的分化及其基本格局：」
                #   第七道闸只查了 a，漏了 b。两行都要查。
                elif line_freq.get(b.rstrip("：:，,。.；;、！!？?　 \t"), 0) >= 5:
                    skip_reasons["下行为页眉"] += 1
                # 2026-11-12 第十四道闸：**下一行是「页码+篇名/书名」式页眉**。
                #   实测坏样例（衣俊卿《现代化与日常生活批判》）：
                #       '…日常生活主体向现代' + '6引子走出日常生活世界的历史涌动'
                #       '…节奏多变'          + '24上篱日常生活的理论范式'
                #       '…主体之中。人们'      + '30上篇口常生活的理论范式'
                #   这类页眉每页页码不同（2/4/6/8/10…），**频次统计永远命中不了**。
                #   判据：行首 1-4 位数字 + 紧跟中文 + 整行 ≤30 字
                #        （正文里数字开头多是年份「1999年第2期」，那种带「年/期」不算）
                elif len(b) <= 30 and re.match(r"^\d{1,4}\s*[\u4e00-\u9fff]", b) \
                        and not re.search(r"年|期|页|月|日", b[:8]):
                    skip_reasons["下行为页码页眉"] += 1
                # 2026-11-11 第八道闸：**下一行太短**。
                #   实测坏样例：`…加以序` + `兰F`（2 字）、`…也当}·也` + `石二4c`
                #   这类下一页首行是碎片（页眉被切剩的一两个字，或 OCR 乱码）。
                #   正文续行极少只有 1-3 字。
                elif len(b) <= 3:
                    skip_reasons["下行太短"] += 1
                # 2026-11-11 第九道闸：**上一行是篇名/书评标题**。
                #   实测坏样例：
                #     `…资` + [`第+八章导言`] + `本的循环…`（页眉夹中间）
                #     `…《德国早期社会政治工人运动史》一书的评论` + `凑材料之匆忙…`（标题当正文）
                #   判据：上一行含书名号《》或〈〉，且以「评论/研究/序言/导言/札记」等结尾。
                elif re.search(r"[《〈＞>].{2,}[》〉]|\）\s*$", a) and \
                        re.search(r"(评论|研究|序言|导言|札记|摘要|批注|一书|版)$", a):
                    skip_reasons["上行是篇名"] += 1
                elif LATIN_TAIL.search(a[-14:] if len(a) >= 14 else a) or LATIN_HEAD.match(b):
                    skip_reasons["外文断行"] += 1
                elif re.search(r"(?:编者注|—编者注|-编者注)\s*$", a):
                    # 编者注结尾是完整的，不能接到下一行正文
                    skip_reasons["编者注尾"] += 1
                elif len(a) < args.min_len:
                    skip_reasons["末行太短"] += 1
                elif TITLE_HEAD.match(b):
                    skip_reasons["首行像标题"] += 1
                # 2026-11-11 第十一道闸：**下一页首行是书眉**。
                #   实测坏样例（L0 抽验 22 条里占 8 条）：
                #     `…每一个因素都跟另一个因素处` + `1857~58年经济学手稿笔记本V政治经济学批判`
                #     `…降低到私人银行`            + `1857—58年经济学手稿笔记本I政治经济学批判`
                #     `…《旗帜晚报孔`               + `英文版序言`
                #     `…德文版第16卷翻译`           + `恩格斯写的1885年第三版序言`
                #     `…上限是5%.[弗·恩.]`          + `第二+二章利润的分割。利息率。…`
                #   这类首行是「年份区间+经济学手稿+笔记本号」的书眉，
                #   或「某某写的/编的…序言」的篇名页。
                #   判据：含年份区间 / 笔记本+罗马数字 / 以序言导言跋结尾。
                elif re.search(r"\d{4}\s*[—\-~至]\s*\d{2,4}\s*年", b) \
                        or re.search(r"(笔记本\s*[IVXⅠⅡⅢⅣⅤ]|经济学手稿)", b) \
                        or re.search(r"(写的|编的|著的).{0,12}(序言|导言|跋)$", b) \
                        or re.search(r"(版序言|版导言|版跋|英文版|德文版)$", b):
                    skip_reasons["下行是书眉"] += 1
                # 2026-11-11 第十二道闸：**下行是「文字+末尾数字」或「数字+文字」的页眉**。
                #   实测拉康选集：每页首行是页眉，奇偶页交替两种形式：
                #       关于《被窃的信》的研讨会5      ← 篇名 + 页码
                #       8拉康选集                    ← 页码 + 书名
                #   这类页眉每页不同（页码变），line_freq 永远命中不了，
                #   于是被当成下一页续行拼进去：
                #       上一行「…使迪」+ 下一行「关于《被窃的信》的研讨会5」
                #       → 「…使迪关于《被窃的信》的研讨会5」  ❌
                #   判据：短行（≤28字）+ 中文字≥3 + （以数字结尾 或 以数字开头）
                elif len(b) <= 28 and len(re.findall(r"[\u4e00-\u9fff]", b)) >= 3 \
                        and (re.search(r"\d[^\d]{0,2}$", b) or re.match(r"^\d", b)):
                    skip_reasons["下行是页码页眉"] += 1
                else:
                    can_join = True

            if can_join:
                out.extend(lines[prev:last_i])       # 上一页，到末行之前
                merged = lines[last_i].rstrip() + b
                out.append(merged)                   # 合并行先出（句子连上）
                out.append(lines[pos])               # 页标记跟在后面
                if len(samples) < 6:
                    samples.append((f.name, a[-24:], b[:24], merged[-40:]))
                out.extend(lines[first_i + 1:nxt])   # 下一页，首行之后
                n_join += 1
            else:
                out.extend(lines[prev:pos])
                out.append(lines[pos])
                out.extend(lines[pos + 1:nxt])
            # prev 必须是「本页正文的结束位置」，不能是 pos。
            # 2026-11-11 踩过：写成 prev=nxt 时它是下一页标记位置，
            # 下一页往回找 range(pos-1, prev-1, -1) 直接为空，全部判成空页。
            prev = nxt
            last_mark = pos

        out.extend(lines[prev:])

        # 2026-11-11 **全局去重**（这是本工具踩过两次的同一个坑，这次根治）：
        #   拼接会把「上一页末行」和「下一页首行」并成一行，
        #   但输出区间控制不严时，原来的两行会有一行残留，
        #   结果同一段话在文件里出现两遍。
        #   实测事故（44卷）：
        #       L412: 应该而且可以向其他国家学习。一个社会即使探索到了本身运动的
        #       L415: 应该而且可以向其他国家学习。一个社会即使探索到了本身运动的自然规律，—
        #   判据：若某行（≥12 字）与**紧邻的后一行**开头 12 字相同，
        #        且这一行更短 → 它是被合并前的残行，删掉。
        #   只删「更短的那个」，长的（合并行）留下。
        dedup = []
        i2 = 0
        n_dup = 0
        while i2 < len(out):
            cur = out[i2].strip()
            nxt2 = out[i2 + 1].strip() if i2 + 1 < len(out) else ""
            # 跳过空行去比较
            j2 = i2 + 1
            while j2 < len(out) and not out[j2].strip():
                j2 += 1
            nxt2 = out[j2].strip() if j2 < len(out) else ""
            if len(cur) >= 12 and len(nxt2) >= 12 \
                    and cur[:12] == nxt2[:12] and len(cur) < len(nxt2):
                n_dup += 1
                i2 += 1
                continue
            dedup.append(out[i2])
            i2 += 1
        out = dedup

        if n_join:
            total_joined += n_join
            per_file.append((str(f.relative_to(BASE)), n_join))
            if not args.dry and args.apply:
                # 2026-11-11：拼完在头部插 JOIN_MARK，下次跑跳过（幂等）。
                newtext = "\n".join(out)
                if newtext.startswith("="):
                    # 头部是 === 分隔块，插在分隔块结束后
                    end = newtext.find("\n" + "=" * 20)
                    if end > 0:
                        newtext = newtext[:end + 1] + JOIN_MARK + "\n" + newtext[end + 1:]
                    else:
                        newtext = JOIN_MARK + "\n" + newtext
                else:
                    newtext = JOIN_MARK + "\n" + newtext
                changed.append((f, raw, newtext))

    print(f"拼接 {total_joined:,} 处跨页断句，涉及 {len(per_file)} 文件\n")
    if already:
        print(f"已拼过而跳过 {len(already)} 文件（幂等保护）：")
        for n in already[:5]:
            print(f"   {n[:56]}")
        if len(already) > 5:
            print(f"   …等 {len(already)} 个")
        print()
    print("跳过原因分布：")
    for k, v in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"  {v:>7,}  {k}")
    print()
    print(f"{'拼接':>6}  文件")
    print("-" * 88)
    for rel, n in sorted(per_file, key=lambda x: -x[1])[:18]:
        print(f"{n:>6}  {rel[-78:]}")

    print(f"\n样例（上页末 → 下页首 → 拼后）：")
    for name, a, b, m in samples:
        print(f"  {name[:26]}")
        print(f"     …{a}  +  {b}…")

    if args.dry or not args.apply:
        print("\n（未写入，加 --apply 才改）")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logp = LOG_DIR / f"join-{stamp}.json"
    logp.write_text(json.dumps({
        "time": stamp, "joined": total_joined,
        "skip_reasons": skip_reasons,
        "per_file": [{"path": p, "joined": n} for p, n in per_file],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    for f, orig, new in changed:
        f.write_text(new, encoding="utf-8")
    print(f"\n已写回 {len(changed)} 个文件")
    print(f"清单：{logp}")


if __name__ == "__main__":
    main()

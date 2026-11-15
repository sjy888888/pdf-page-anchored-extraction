# -*- coding: utf-8 -*-
"""
pdf2txt_paged.py v8 —— 带文字层 PDF → 「实测印刷页码 + 正文」纯文本

版本史（每条都是实测踩出来的，别回退）：
  v1  全局偏移。前置说明一套页码、正文另一套 → 错位。
  v2  按文本「行」找纯数字行。把正文年份 1848 当页码（文本行 ≠ 版面位置）。
  v3  block 级 + 固定比例版面区。马恩文集 0.92；列宁全集 0 命中。
  v4  放宽比例区。列宁 0.72，双位页码拆成两个数。
  v5  span 按 x 拼行。列宁 0.81；马恩文集出现「正文首行落进 20% 区」误取。
  v6  改用 PDF 自带 line 结构 → 列宁掉到 0.14（页码两位被存成两个 line）。
  v7  v5 聚类 + 标点过滤 + 众数二次过滤。列宁 0.61，仍不够：
      页码 span 和章标题 span 共享同一 y，「按 x 拼行」把 50 和标题粘成一行，
      「５０１９１５年９月…」这种串既不是合法页码，也过不了标点过滤。
  v8（本版）**只追纯数字 span 群**，不拼整行：
      1. 取所有「纯数字 span」（全角转半角，长度 1–4）
      2. 只保留版面页眉区（y1 < 0.20H）或页脚区（y0 > 0.85H）里的
      3. 同 y 带（±2pt）内、x 相邻（间隙 < 14pt）的数字 span 拼成一个数：
         「５」(x95) + 「０」(x99) → 50
      4. 生成器水印整块跳过（S22PDF / pdf@home / 本PDF文件由）
      5. 众数偏移二次过滤：|pdf页 − 印刷页 − mode_delta| > band 丢弃；无合规就写【p?】，不猜

实测（2026-11-09，各卷前 120–300 页）：
  马恩文集 1 卷 0.863（mode 21）；列宁全集文字版 27 卷 0.98

体例：
  【p16】[PDFp40]    印刷页码实测 16
  【p?|pdf13】       本页无可信页码
"""
import re
import sys
import json
import argparse
import datetime
from collections import Counter
from pathlib import Path

import pymupdf

FULL2HALF = {ord(c): ord(h) for c, h in zip("０１２３４５６７８９", "0123456789")}
DIGITS_ONLY = re.compile(r"^\d{1,4}$")


def is_junk(t):
    packed = t.replace(" ", "").replace("\u3000", "")
    return ("S22PDF" in packed) or ("pdf@home" in packed) or ("icm.ac.cn" in packed) \
        or packed.startswith("本PDF文件由")


def digit_span_groups(page, first_band=0.20, last_band=0.80, ytol=2.0, xgap=14.0):
    """页眉/页脚区里的纯数字 span，按 y 聚类 + x 相邻拼接。返回 [(zone, value)]"""
    H = page.rect.height
    spans = []
    try:
        dd = page.get_text("dict")
    except Exception:
        return []
    for b in dd.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for s in ln.get("spans", []):
                t = s.get("text", "")
                if not t.strip():
                    continue
                if is_junk(t):
                    continue
                x0, y0, x1, y1 = s["bbox"]
                # 2026-11-10 四修：页码区判定改为「距页边比例 + 绝对距离」双条件。
                # 事故链（同一个根因，改了四次才彻底）：
                #   ① last_band=0.85，MEGA 普通卷页码 0.84 → 全漏
                #   ② 改 0.80，MEGA 大卷（页高 1191）页码 0.746 → 又漏
                #   ③ 加绝对距离，MEGA¹ III/4 页码在**页首** 0.24 → 还漏
                # 教训：不同书的页码位置差异极大（页首 0.24 / 页中 0.75 / 页脚 0.84）。
                # 现在：页首区 = 距页顶 30% 内；页脚区 = 距页底 30% 内。
                # 两者合计覆盖上下各 30%，中间 40% 才是正文区。
                # 这么宽的代价是可能误收正文里的数字，但 digit_span_groups 后面
                # 还有「必须纯数字 + 长度≤4 + 拼接后 1–2000 + 与其它页偏移一致」多重过滤。
                HEAD_FRAC = 0.30
                FOOT_FRAC = 0.30
                if y1 < H * HEAD_FRAC:
                    zone = "head"
                elif (H - y0) < H * FOOT_FRAC:
                    zone = "foot"
                else:
                    continue
                for part in t.translate(FULL2HALF).split():
                    pass
                tt = t.translate(FULL2HALF).strip()
                if not DIGITS_ONLY.match(tt):
                    continue
                # 2026-11-10 修：把字号也带进去。
                # 事故：全集二版第33卷页首同一行有「42」「1861」「63」三个数字
                # （42=印刷页，1861/63=年份与卷号水印），字号不同但 y 相邻，
                # 被 xgap 拼成「4263」→ 超 4 位 → 整页丢弃 → 实测率只有 42.3%。
                # 现在拼接时要求字号一致。
                sz = round(s.get("size", 0), 1)
                spans.append((zone, y0, x0, x1, tt, sz))

    if not spans:
        return []

    out = []
    for zone in ("head", "foot"):
        group = sorted([s for s in spans if s[0] == zone], key=lambda z: (z[1], z[2]))
        i = 0
        while i < len(group):
            y0, x0, x1, txt, sz0 = group[i][1], group[i][2], group[i][3], group[i][4], group[i][5]
            j = i + 1
            while j < len(group):
                ny0, nx0, nx1, ntxt, nsz = group[j][1], group[j][2], group[j][3], group[j][4], group[j][5]
                # 字号差 > 1.5pt 视为不同元素，不拼（2026-11-10：防「42」+「1861」拼成「4263」）
                if abs(ny0 - y0) <= ytol and nx0 - x1 <= xgap and abs(nsz - sz0) <= 1.5:
                    txt += ntxt
                    x1 = nx1
                    j += 1
                else:
                    break
            v = int(txt) if txt.isdigit() and len(txt) <= 4 else None
            if v is not None and 1 <= v <= 2000:
                # 2026-11-10 五修（用户实测「p? 仍然存在」）：
                #   事故：全集第33卷（1861-63手稿），印刷页码印在页面最顶（如 12/13/14），
                #   但正文里大量引用「同上第246页」，这些引文页码也落在页首区，
                #   投票阶段被引文页码带偏，整卷 234 页（56%）判成 p?。
                # 判据：**区分「极外缘」与「近正文」**。
                #   页码必然贴着页边印（距页顶 <12% 或距页底 <12%）；
                #   正文引文里的数字离页边更远（12%–30%）。
                #   投票时优先用极外缘那个；没有才退回近边的。
                frac_top = y0 / H
                frac_bot = (H - y1) / H
                edge = "far" if (frac_top < 0.12 or frac_bot < 0.12) else "near"
                out.append((zone, v, edge))
            i = j
    # 同页同 zone 里若有两个，极外缘优先排前
    out.sort(key=lambda t: (0 if t[2] == "far" else 1))
    return [(z, v, e) for z, v, e in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--book", default="")
    ap.add_argument("--vol", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--isbn", default="")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--tol", type=int, default=90, help="首轮粗筛 |pdf页-页码| 上限")
    ap.add_argument("--band", type=int, default=8, help="次轮 |delta-mode| 上限")
    ap.add_argument("--chunk", type=int, default=0,
                    help="分段窗口（0=整卷一次投票）。整卷投票失效时用它："
                         "前后几段版式不同时（如书信卷 目录/正文 各一套页码），"
                         "整卷众数会被目录吸走，分段后各段自投各的。")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    end = args.end if args.end > 0 else total
    end = min(end, total)
    rng = list(range(args.start, end + 1))

    raw = {}
    for pno in rng:
        raw[pno] = digit_span_groups(doc.load_page(pno - 1))

    # ── 投票定 mode_delta ──
    # 整卷一次投票（默认）或分段投票（--chunk N）。
    # 文集第 10 卷（书信）踩过：目录区与正文区的页码各成一套，整卷投票被目录吸走，
    # 实测率掉到 0.742；分段后 0.94。这不是调参问题，是「一册书里有几套页码」的事实。
    def vote(pages):
        c = Counter()
        for pno in pages:
            best = None
            for zone, v, edge in raw[pno]:
                if abs(pno - v) > args.tol:
                    continue
                if best is None or abs(pno - v) < abs(pno - best[1]):
                    best = (zone, v, edge)
            if best:
                c[pno - best[1]] += (3 if best[2] == "far" else 1)
        return (c.most_common(1)[0] if c else (0, 0)), c

    if args.chunk > 0:
        delta_of = {}
        chunks = [rng[i:i + args.chunk] for i in range(0, len(rng), args.chunk)]
        for ch in chunks:
            (md, mv), _ = vote(ch)
            for p in ch:
                delta_of[p] = md
        mode_delta, mode_votes = delta_of.get(rng[0], 0), sum(1 for _ in chunks)
    else:
        (mode_delta, mode_votes), _ = vote(rng)
        delta_of = {p: mode_delta for p in rng}

    parts, idx, stat = [], [], Counter()
    resets, prev = [], None
    # 2026-11-10 七修：先收集每页的「印刷页码 / None」，全部跑完再统一推断。
    #   影印本里个别页真的没印页码（全33卷有 234 页分散缺），
    #   但如果**前后两页页码连续、中间正好缺 N 页，缺的页码就是算术可推的**：
    #   pdf p-1 是 N，pdf p+k 是 N+k，那 p 就是 N+1。
    #   这是算术不是猜。判据严：①前后都有页码 ②差值恰好等于页距 ③中间每页都无页码。
    #   任一条不满足就保留 p?，绝不硬补。
    picked = {}
    for pno in rng:
        page = doc.load_page(pno - 1)
        txt = page.get_text("text")
        has_text = bool(txt.strip())

        pick, pick_key = None, None
        md_here = delta_of.get(pno, mode_delta)
        for zone, v, edge in raw[pno]:
            d = pno - v
            if abs(d - md_here) > args.band:
                continue
            # 2026-11-10 六修：**偏移吻合度优先，区域次之，外缘第三**。
            # 事故：全集第33卷 pdf33 有 head=12（真页码，d=21 与全卷众数一致）
            # 和 foot=9（引文页码，d=24 差 3）；原 key 先比 zone（foot 优先），
            # 于是选了 9，整卷 224 页判不出来。
            # 现在顺序：① 偏移差值 ② far 外缘 ③ head 优于 foot。
            key = (
                abs(d - md_here),
                0 if edge == "far" else 1,
                0 if zone == "head" else 1,
            )
            if pick_key is None or key < pick_key:
                pick, pick_key = (zone, v, edge), key
        picked[pno] = {
            "printed": pick[1] if pick else None,
            "zone": pick[0] if pick else None,
            "edge": pick[2] if pick else None,
            "txt": txt,
            "has_text": has_text,
        }

    # ── 七修：连续页推断 ──
    inferred = 0
    for pno in rng:
        if picked[pno]["printed"] is not None:
            continue
        # 向前找最近的有页码页
        a = pno - 1
        while a >= rng[0] and picked[a]["printed"] is None:
            a -= 1
        # 向后找
        b = pno + 1
        while b <= rng[-1] and picked[b]["printed"] is None:
            b += 1
        if a < rng[0] or b > rng[-1]:
            continue
        pa, pb = picked[a]["printed"], picked[b]["printed"]
        # 判据：前后页码差 == 页距，且中间每页都无页码（已由循环保证）
        if pb - pa == b - a:
            picked[pno]["printed"] = pa + (pno - a)
            picked[pno]["zone"] = "inferred"
            picked[pno]["edge"] = "far"
            inferred += 1

    # ── 九修（2026-11-10，按用户定的双轨制重写）──
    #   用户原话：「轨 A：PDF 页序号（永远可靠）。轨 B：印刷页码（可能失败）。
    #     规则：B 值与前一页连续 → 采用 B；B 值回退或跳号 → 标 [p_待核]，只留 A；
    #     全卷 B 值混乱率 > 5% → 整卷改用 A，并在文件头声明。
    #     宁可标「待核」，不许给一个假页码。假页码比没有页码更危险。」
    #
    #   这才是对的。我之前的「八修」只看整体链，逐页的孤立错误照样留着；
    #   而且从没做过「整卷降级」这一步。
    # ── 十修（2026-11-10，用户曝出文集02 开头仍是 2,7,8...）──
    #   九修的双轨制只判「回退/跳号」，但**卷首那几页没有前一页可比**，
    #   于是扉页上那个「2」（其实是版权页的「第二卷」）照样被当成页码。
    #   判据：正文开始前的卷首区（封面/版权/目录/序）一律不给页码。
    #   做法：找到第一个 PageReset（目录结束、正文起）或前 60 页内的
    #        第一个「连续 5 页以上递增」的起点，之前全部清空。
    head_cut = rng[0] - 1
    run = []
    for pno in rng:
        v = picked[pno]["printed"]
        if v is not None and (not run or v > run[-1][1]):
            run.append((pno, v))
            if len(run) >= 5:
                # 2026-11-10 十一修：head_cut 要用 run[0][0]（含），
                # 原来写 -1 把第一个「看着像页码」的也给留下了，
                # 于是文集02 扉页那个「2」（其实是版权页的「第二卷」）还在。
                head_cut = run[0][0]
                break
        else:
            run = [(pno, v)] if v is not None else []
    # 只在前 80 页内做这个判断（正文开始得早）
    if head_cut > 0 and head_cut <= 80:
        for pno in rng:
            if pno <= head_cut:
                picked[pno]["printed"] = None
                picked[pno]["zone"] = None

    bad_pages = []
    prev_b = None
    prev_p = None
    for pno in rng:
        v = picked[pno]["printed"]
        if v is None:
            prev_b = None          # 无页码页打断连续性，下一页不判连续
            prev_p = pno
            continue
        if prev_b is not None:
            gap = pno - prev_p
            if v <= prev_b or (v - prev_b) > gap + 1:
                # 回退或跳号 —— 不可信，降级为待核
                bad_pages.append({"pdf": pno, "val": v, "prev": prev_b})
                picked[pno]["printed"] = None
                picked[pno]["zone"] = None
                prev_b = None
                prev_p = pno
                continue
        prev_b, prev_p = v, pno

    # 全卷混乱率判定
    measurable = sum(1 for p in rng if picked[p]["printed"] is not None) + len(bad_pages)
    chaos = len(bad_pages) / measurable if measurable else 0.0
    WHOLE_VOLUME_DOWNGRADE = chaos > 0.05
    if WHOLE_VOLUME_DOWNGRADE:
        for p in rng:
            if picked[p]["printed"] is not None:
                picked[p]["printed"] = None
                picked[p]["zone"] = "whole-volume-downgrade"

    for pno in rng:
        d = picked[pno]
        txt, has_text, printed = d["txt"], d["has_text"], d["printed"]
        if printed is not None:
            stat["measured"] += 1
            prev = printed
            tag = f"【p{printed}】"
        else:
            stat["unmeasured" if has_text else "empty"] += 1
            # 2026-11-10 十二修（用户：「如果没有页码，就不标了，宁无不假」）
            #   原来无页码页写【p?|pdfN】，那个 p? 是个占位垃圾，
            #   检索和引用时都得再判一次「这是不是问号」。
            #   现在：无页码页**什么都不标**，只留 [PDFpN]（轨 A，永远可靠）。
            #   宁可让用户自己翻，也不给一个看起来像页码的东西。
            tag = ""

        parts.append(f"{tag}[PDFp{pno}]")
        if not has_text:
            parts.append("〔本页无可抽取文字层：插图/图版/空白页，待人工核〕")
        else:
            body = txt.replace("\r\n", "\n").replace("\r", "\n")
            for ln in body.split("\n"):
                parts.append(ln.rstrip())
        parts.append("")

        first = ""
        for ln in txt.split("\n"):
            s = ln.strip()
            if s:
                first = s[:70]
                break
        idx.append({"pdf_page": pno, "printed": printed,
                    "zone": d["zone"],
                    "chars": len(txt.strip()), "first": first})

    with_text = stat["measured"] + stat["unmeasured"]
    ratio = stat["measured"] / with_text if with_text else 0

    header = [
        "=" * 74,
        f"著作名：{args.book}",
        f"出处卷次：{args.vol}",
        (f"ISBN：{args.isbn}" if args.isbn else "ISBN：（未录）"),
        f"来源文件：{Path(args.pdf).name}（PDF 共 {total} 页）",
        f"来源 URL：{args.source}",
        f"抽取日期：{datetime.date.today().isoformat()}",
        "字源：PDF 文字层（出版方排版文件；个别卷是 Acrobat 扫校件，以本行实测率为准）。",
        # 2026-11-10 九修：双轨制声明（用户定）
        "页码双轨：【PDFpN】= 轨 A，PDF 页序号，永远可靠；【pN】= 轨 B，该页印刷页码。",
        "  只有【pN】可与【PDFpN】并存；B 值回退或跳号的一律降级为【p?|pdfN】。",
        "【p?|pdfN】= 该页印刷页码不可信或不存在，只能按 PDF 页序号引，引用写「页码待核」。",
        f"页码实测率：{stat['measured']}/{with_text} = {ratio:.1%}；众数偏移（PDF页-印刷页）= {mode_delta}，命中 {mode_votes} 页。",
        f"序列连续性：剔除 {len(bad_pages)} 个回退/跳号页码，全卷混乱率 {chaos:.1%}"
        + ("，**已整卷降级为轨 A**（混乱率 > 5%）。" if WHOLE_VOLUME_DOWNGRADE else "（≤5%，逐页保留可信页码）。"),
        "引用规则：只有【pN】的页码可直接引；【p?】一律写「页码待核」。**宁可待核，不许给假页码。**",
        "=" * 74,
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + [""] + parts), encoding="utf-8")

    sidecar = out.with_suffix(out.suffix + ".pages.json")
    sidecar.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out),
        "pages": len(rng),
        "measured": stat["measured"], "unmeasured": stat["unmeasured"], "empty": stat["empty"],
        "measured_ratio": round(ratio, 3),
        "mode_delta": mode_delta, "mode_votes": mode_votes,
        "page_resets": resets[:6],
        "bytes": out.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

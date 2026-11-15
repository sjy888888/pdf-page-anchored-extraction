# -*- coding: utf-8 -*-
"""
ocr_pdf_paged.py —— 纯扫描件 PDF → 带实测页码标记的 txt（RapidOCR）

为什么需要（2026-11-09 实测）：
  L2 西方马克思主义 / L4 苏东阐释 这批书**全是扫描件**，文字层是空的：
  卢卡奇《历史与阶级意识》454 页 0 字、阿尔都塞《保卫马克思》281 页 0 字、
  普列汉诺夫哲学著作选集 958 页 0 字……共 44 种，一个字符都抽不出来。
  而 `pdf2txt_paged.py` 只对**有文字层**的 PDF 有效。所以必须补 OCR 这一条路。

设计（跟 reflow_text.py 同构，只把「取文字层」换成「OCR」）：
  1. 逐页渲染 200dpi 灰度图（中文正文 200dpi 是精度/速度的折中，150 会丢笔画）
  2. RapidOCR 逐页识别，拿 (text, box)
  3. 按 box 的 y 聚类成行，行内按 x 排序拼接——中文不加空格
  4. 页码：只认页脚区（y > 88% 页高）的纯数字行，众数投票定偏移
  5. 断点续跑：已完成页写进 .jsonl，重跑跳过

用法:
  python ocr_pdf_paged.py <pdf> <out.txt> --book "书名" [--start 1] [--end 0] [--dpi 200]

成本参考（本机实测，200dpi）：约 1.5–3 秒/页，500 页 ≈ 25 分钟。
"""
import io
import re
import sys
import json
import time
import argparse
from pathlib import Path
from collections import Counter

import numpy as np
import pymupdf
from rapidocr_onnxruntime import RapidOCR

FULL2HALF = {ord(c): ord(h) for c, h in zip("０１２３４５６７８９", "0123456789")}
NUM_ONLY = re.compile(r"^[\s·•\-—–]*(\d{1,4})[\s·•\-—–]*$")


def render_gray(page, dpi=200):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return img


def ocr_page(engine, img):
    res, _ = engine(img)
    if not res:
        return []
    out = []
    for item in res:
        box, text = item[0], item[1]
        if not text or not text.strip():
            continue
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        out.append({
            "t": text.strip(),
            "y": sum(ys) / len(ys),
            "x": min(xs),
            "x2": max(xs),
            "h": max(ys) - min(ys),
        })
    return out


def group_lines(items):
    """按 y 聚成视觉行；容差 = 行高中位数的一半"""
    if not items:
        return []
    hs = [i["h"] for i in items if i["h"] > 0]
    med = sorted(hs)[len(hs) // 2] if hs else 10
    tol = max(med * 0.5, 4)
    items = sorted(items, key=lambda z: (z["y"], z["x"]))
    lines, cur, cur_y = [], [], None
    for it in items:
        if cur_y is None or abs(it["y"] - cur_y) <= tol:
            cur.append(it)
            cur_y = it["y"] if cur_y is None else cur_y
        else:
            lines.append(sorted(cur, key=lambda z: z["x"]))
            cur, cur_y = [it], it["y"]
    if cur:
        lines.append(sorted(cur, key=lambda z: z["x"]))
    return lines


def detect_folio(lines, page_h, pno=None, tol=None):
    """找印刷页码。

    2026-11-09 四次修，每次都是判据太松或坐标口径错：
      ① 只认页脚区 → 《历史与阶级意识》页码在页首，实测率 0。
      ② 两区都认，仍 0 → page_h 传的是 PDF 的 pt(841)，OCR 的 y 是 200dpi 像素(2339)。
      ③ 改传图片高度 → 0.4，但出现「行首数字」误判：
         页眉是「新版序言（1967）」，行首数字 1 被当成页码 1，算出偏移 43。
      ④ 本版：**页码候选必须与已定的众数偏移一致**（|pno - v - mode_delta| ≤ tol）。
         偏移由整卷投票先算出来，再回填到每页——单页孤证不足以定页码。
    """
    if not lines:
        return None
    cand = []
    for ln in reversed(lines):
        y = ln[0]["y"]
        if y < page_h * 0.85:
            break
        txt = "".join(i["t"] for i in ln).translate(FULL2HALF).strip()
        m = NUM_ONLY.match(txt)
        if m:
            cand.append(int(m.group(1)))
    for ln in lines[:3]:
        y = ln[0]["y"]
        if y > page_h * 0.45:
            break
        txt = "".join(i["t"] for i in ln).translate(FULL2HALF).strip()
        m = NUM_ONLY.match(txt)
        if m:
            cand.append(int(m.group(1)))
            continue
        # 「18新版序言」这种只有**恰好两位**才可能是页码，单字符行首数字一律不要
        m = re.match(r"^(\d{2,4})\s*\D", txt)
        if m:
            cand.append(int(m.group(1)))

    cand = [v for v in cand if 1 <= v <= 2000]
    if not cand:
        return None
    if pno is None or tol is None:
        return cand[0]
    # 有偏移参照时，只取与参照一致的那个
    for v in cand:
        if abs((pno - v) - tol) <= 12:
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--book", default="")
    ap.add_argument("--vol", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--chunk", type=int, default=0)
    ap.add_argument("--threads", type=int, default=2,
                    help="onnxruntime 线程数上限。**默认 2，不要随便调大。**")
    ap.add_argument("--pause", type=float, default=0.15,
                    help="每页之间停顿秒数，给别的程序让出一口气")
    args = ap.parse_args()

    # ── CPU 限流（2026-11-09 事故后加的死限制）──
    # 事故经过：同时开 5 条 OCR 队列，每条都让 onnxruntime 按物理核数拉满（16核×5=80线程），
    # 内存被吃到只剩几百 MB，机器直接卡死、用户被迫重启。
    # 这三行必须在 RapidOCR() 之前执行，否则 onnxruntime 已经把线程池建好了。
    #
    # 2026-11-10 改：原来 `min(args.threads, 8)` 把上限卡死 8，
    #   用户要求「CPU 加到 90%」，但传 12 也被截成 8，16 核里 8 核永远闲着（实测 CPU 87%）。
    #   现在：上限放到 **核数的一半**（16核 → 8 是安全线，16 核全给会拖慢单页）。
    #   实测依据：
    #     · 8 线程 → 10 秒/页，CPU 87%
    #     · 12 线程 → 15 秒/页，CPU 100%（更慢）
    #   说明这台机器上「单队列超过 8 线程不划算」。真正的提速要靠**多队列跑不同的书**，
    #   而不是把单队列的线程数堆上去。所以上限保持 8，但允许显式覆盖。
    import os
    _cap = 8 if not os.environ.get("OCR_ALLOW_OVERSUBSCRIBE") else 32
    n = max(1, min(args.threads, _cap))
    os.environ["OMP_NUM_THREADS"] = str(n)
    os.environ["ORT_NUM_THREADS"] = str(n)
    os.environ["OPENBLAS_NUM_THREADS"] = str(n)
    os.environ["MKL_NUM_THREADS"] = str(n)
    try:
        import onnxruntime as _ort
        _so = _ort.SessionOptions()
        _so.intra_op_num_threads = n
        _so.inter_op_num_threads = 1
        _ort.set_default_logger_severity(3)
    except Exception:
        pass
    try:
        import torch
        torch.set_num_threads(n)
    except Exception:
        pass

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    end = args.end if args.end > 0 else total
    end = min(end, total)
    rng = list(range(args.start, end + 1))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache = out.with_suffix(out.suffix + ".ocr.jsonl")

    done = {}
    if cache.exists():
        for ln in cache.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(ln)
                done[rec["pdf_page"]] = rec
            except Exception:
                pass
        print(f"[resume] 已有 {len(done)} 页缓存", flush=True)

    engine = RapidOCR()
    t0 = time.time()
    todo = [p for p in rng if p not in done]
    print(f"[ocr] 待处理 {len(todo)} / {len(rng)} 页，dpi={args.dpi}，线程={n}", flush=True)

    with open(cache, "a", encoding="utf-8") as fh:
        for k, pno in enumerate(todo, 1):
            try:
                _t_page = time.time()
                page = doc.load_page(pno - 1)
                img = render_gray(page, args.dpi)
                H = float(img.shape[0])   # 用图片高度，不是 PDF 的 pt
                items = ocr_page(engine, img)
                lines = group_lines(items)
                folio = detect_folio(lines, H)   # 先取孤证，用于投票
                rec = {
                    "pdf_page": pno,
                    "printed": folio,
                    "lines": ["".join(i["t"] for i in ln).translate(FULL2HALF) for ln in lines],
                }
                done[pno] = rec
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                # 让出一口气：每页停一下，避免长时间 100% 占满把机器拖死
                _dt = time.time() - _t_page
                if _dt > 120:
                    print(f'  [slow] pdf{pno} 用了 {_dt:.0f} 秒（图版页？）', flush=True)
                if args.pause > 0:
                    time.sleep(args.pause)
            except Exception as e:
                done[pno] = {"pdf_page": pno, "printed": None,
                             "lines": [f"〔OCR 失败：{str(e)[:60]}〕"]}
            if k % 20 == 0 or k == len(todo):
                el = time.time() - t0
                print(f"  {k}/{len(todo)}  已用 {el/60:.1f} 分  "
                      f"均 {el/max(k,1):.2f} 秒/页  剩余约 {(len(todo)-k)*el/max(k,1)/60:.0f} 分",
                      flush=True)

    # 页码众数校准
    deltas = Counter()
    for p in rng:
        r = done.get(p)
        if r and r.get("printed"):
            # 2026-11-15 修：printed 可能是字符串（红旗的「1965年·总4·p2」）
#   原来直接做减法会 TypeError。非整数就跳过众数偏移统计。
                _pv = r["printed"]
                if isinstance(_pv, int):
                    deltas[_pv - p] += 1
    mode_delta = deltas.most_common(1)[0][0] if deltas else None

    parts, idx, measured, bad = [], [], 0, 0
    for p in rng:
        r = done.get(p) or {"printed": None, "lines": []}
        printed = r.get("printed")
        if printed and mode_delta is not None and abs((p - printed) - mode_delta) > 12:
            printed = None
        if printed:
            measured += 1
            tag = f"【p{printed}】"
        else:
            bad += 1
            tag = f"【p?|pdf{p}】"
        parts.append(f"{tag}[PDFp{p}]")
        lines = r.get("lines") or []
        if not lines:
            parts.append("〔本页 OCR 无结果：插图/空白页，待人工核〕")
        else:
            parts.extend(lines)
        parts.append("")
        idx.append({"pdf_page": p, "printed": printed,
                    "first": (lines[0][:60] if lines else "")})

    header = [
        "=" * 74,
        f"著作名：{args.book}",
        f"出处卷次：{args.vol}",
        f"来源文件：{Path(args.pdf).name}（PDF 共 {total} 页）",
        f"来源 URL：{args.source}",
        f"处理日期：{time.strftime('%Y-%m-%d')}",
        f"处理方式：**OCR**（RapidOCR，{args.dpi}dpi 灰度渲染）。原 PDF 无文字层。",
        "字源：OCR 识别结果，未经人工逐字校订；错字按原样保留，不静默改字。",
        "页码体例：【pN】= 页脚区实测印刷页码；【p?|pdfN】= 无页码，只记 PDF 页序。",
        f"页码实测率：{measured}/{max(len(rng),1)} = {measured/max(len(rng),1):.1%}"
        + (f"；众数偏移 = {mode_delta}" if mode_delta is not None else "；未能定偏移"),
        "引用规则：只有【pN】的页码可直接引；【p?】的段落引用写「页码待核」。",
        "=" * 74,
    ]
    out.write_text("\n".join(header + [""] + parts), encoding="utf-8")
    out.with_suffix(out.suffix + ".pages.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out), "pages": len(rng),
        "measured": measured, "unmeasured": bad,
        "measured_ratio": round(measured / len(rng), 3),
        "mode_delta": mode_delta,
        "minutes": round((time.time() - t0) / 60, 1),
        "bytes": out.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

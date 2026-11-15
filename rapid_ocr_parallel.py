"""rapid_ocr_jsonl.py —— 页级并行版 RapidOCR

2026-11-10 改：原来单线程串行，16 核只用 8 核，且 --threads 参数根本没被消费。
  实测证据：
    · 代码是 `for name in pngs: ocr(img)` —— 一页一页排队
    · RapidOCR(intra_op_num_threads=8) 硬编码，只影响**单张图内部**的并行
    · 所以 16 核里 8 核在算一张图，8 核闲着
    · 传 --threads 12 无效，反而因为外层并发抢核导致更慢（10→15 秒/页）

现在：**4 进程 × 每进程 4 个 intra 线程 = 16 核吃满**。

Usage: python rapid_ocr_jsonl.py <png_dir> <out.jsonl> [pages_per_log] [workers]
Resumable: pages already present in <out.jsonl> are skipped.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
import numpy as np

png_dir = sys.argv[1]
out_jsonl = sys.argv[2]
log_every = int(sys.argv[3]) if len(sys.argv) > 3 else 20
WORKERS = int(sys.argv[4]) if len(sys.argv) > 4 else 4
INTRA = max(1, 16 // WORKERS)          # 每进程分到的核

_ocr = None


def get_ocr():
    """每个 worker 进程各建一个 RapidOCR，intra 线程数按核数均分"""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR(use_cls=False, rec_batch_num=16,
                        intra_op_num_threads=INTRA, inter_op_num_threads=1)
    return _ocr


def ocr_one(args):
    """处理一页，返回 (page, lines, w, h) 或错误"""
    path, page = args
    try:
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return page, None, 0, 0, "decode-fail"
        h, w = img.shape[:2]
        res = get_ocr()(img)
        if isinstance(res, tuple) and len(res) == 2:
            res = res[0]
        lines = []
        for item in (res or []):
            try:
                box, txt, score = item[0], str(item[1]), float(item[2])
                ys = [float(p[1]) for p in box]
                xs = [float(p[0]) for p in box]
                lines.append({"t": txt, "x0": round(min(xs), 1), "y0": round(min(ys), 1),
                              "x1": round(max(xs), 1), "y1": round(max(ys), 1)})
            except Exception:
                continue
        lines.sort(key=lambda l: (l["y0"], l["x0"]))
        return page, lines, w, h, None
    except Exception as e:
        return page, None, 0, 0, str(e)[:60]


def main():
    done = set()
    if os.path.exists(out_jsonl):
        with open(out_jsonl, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["page"])
                except Exception:
                    pass

    pngs = sorted(n for n in os.listdir(png_dir) if n.lower().endswith(".png"))
    todo = []
    for name in pngs:
        m = re.match(r"p(\d+)", name)
        page = int(m.group(1)) if m else 0
        if page in done or page == 0:
            continue
        todo.append((os.path.join(png_dir, name), page))

    print(f"[rapid] {len(pngs)} pages, already done {len(done)}, todo {len(todo)}"
          f"，{WORKERS} 进程 × {INTRA} 线程", flush=True)

    if not todo:
        print("[rapid] nothing to do", flush=True)
        return

    t0 = time.time()
    proc = 0
    results = []
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(ocr_one, t) for t in todo]
        for fu in as_completed(futs):
            page, lines, w, h, err = fu.result()
            if err:
                print(f"[rapid] page {page} ERROR {err}", flush=True)
                continue
            results.append((page, lines, w, h))
            proc += 1
            # 攒一批再写，减少 IO
            if len(results) >= 20:
                results.sort()
                with open(out_jsonl, "a", encoding="utf-8") as out:
                    for p, ls, ww, hh in results:
                        out.write(json.dumps({"page": p, "w": ww, "h": hh,
                                              "engine": "rapid", "lines": ls},
                                             ensure_ascii=False) + "\n")
                results = []
            if proc % log_every == 0:
                el = time.time() - t0
                per = el / proc
                left = (len(todo) - proc) * per
                print(f"[rapid] {proc}/{len(todo)}  {per:.2f}s/page  "
                      f"elapsed={el/60:.1f}min  eta={left/60:.1f}min", flush=True)

    if results:
        results.sort()
        with open(out_jsonl, "a", encoding="utf-8") as out:
            for p, ls, ww, hh in results:
                out.write(json.dumps({"page": p, "w": ww, "h": hh,
                                      "engine": "rapid", "lines": ls},
                                     ensure_ascii=False) + "\n")

    el = time.time() - t0
    print(f"[rapid] DONE processed={proc} in {el/60:.1f}min -> {out_jsonl}", flush=True)


if __name__ == "__main__":
    main()

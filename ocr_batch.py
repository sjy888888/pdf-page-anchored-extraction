# -*- coding: utf-8 -*-
"""
ocr_batch.py —— 批量 OCR：扫描件 PDF → 分层目录里的带页码 txt

为什么单独写（2026-11-09）：
  L2/L4 共 54 种书全是扫描件（文字层 0 字符）。`extract_layer.py` 只能处理有文字层的，
  这批必须走 OCR。而 OCR 一本 500 页 ≈ 45 分钟，必须**断点续跑 + 后台跑 + 逐本落盘**，
  不能一键全跑完再写文件——一旦中断就全丢。

设计：
  · 每本单独起进程，跑完一本写一本
  · 用 `.ocr.jsonl` 做页级缓存，中断重跑只补没跑的页
  · 实测率 < 0.3 的单独列出来（有些书只有奇数页有页码，那是书上就这样，不是错）

用法:
  python ocr_batch.py --list 清单.json [--layer L2] [--dpi 200]
清单项:
  {"pdf":"...", "out":"书名.txt", "book":"书名", "vol":"版本", "layer":"L2"}
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"G:\path\to\knowledge-base\学科资料")
TOOL = Path(r"G:\path\to\knowledge-base\工具\ocr_pdf_paged.py")
PY = sys.executable


def layer_dir(prefix):
    hit = [d for d in ROOT.iterdir() if d.is_dir() and d.name.startswith(prefix)]
    return hit[0] if hit else None


def _pid_alive(pid):
    """跨平台判 pid 是否存活。

    2026-11-09 修：原实现用 `psutil.pid_exists`，但本机**没装 psutil**，
    异常被吞掉 → alive 永远是 False → 单实例锁形同虚设 →
    同时跑起 2 条队列，16 核被吃满，机器卡死重启。
    现在：Windows 用 tasklist，其它平台用 os.kill(pid, 0)。
    """
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=10)
            return str(pid) in (r.stdout or "")
        import os as _os
        _os.kill(pid, 0)
        return True
    except Exception:
        return False



def _mem_ok(need_mb=1500):
    """内存守门：可用物理内存低于 need_mb 就拒绝启动新队列。

    2026-11-09 事故教训：5 条队列把 16 核拉满、内存只剩几百 MB，机器卡死重启。
    用户后来说「90% 没问题」——那是对 CPU 说的，**内存是另一条线**。
    本机 15.8 GB，DeepSeek Harness 自己就占 3 GB，留 1.5 GB 余量是底线。
    """
    try:
        import subprocess as _sp
        r = _sp.run(["wmic", "OS", "get", "FreePhysicalMemory", "/value"],
                    capture_output=True, text=True, timeout=10)
        for line in (r.stdout or "").splitlines():
            if "=" in line:
                kb = int(line.split("=")[1].strip())
                return kb / 1024 >= need_mb
    except Exception:
        pass
    return True


def _pin_high_priority():
    """把自己提到 High 优先级，减少被别的进程抢核导致的空转。"""
    try:
        import psutil  # 本机没装，会走下面的分支
        import os as _os
        psutil.Process(_os.getpid()).nice(psutil.HIGH_PRIORITY_CLASS)
    except Exception:
        try:
            import subprocess as _sp
            import os as _os
            _sp.run(["powershell", "-NoProfile", "-Command",
                     f"(Get-Process -Id {_os.getpid()}).PriorityClass='High'"],
                    capture_output=True, timeout=10)
        except Exception:
            pass

def main():
    args = sys.argv[1:]
    lst = json.loads(Path(args[args.index("--list") + 1]).read_text(encoding="utf-8"))
    dpi = args[args.index("--dpi") + 1] if "--dpi" in args else "200"
    only = args[args.index("--only") + 1] if "--only" in args else None
    threads = args[args.index("--threads") + 1] if "--threads" in args else "2"
    pause = args[args.index("--pause") + 1] if "--pause" in args else "0.15"

    # ── 并发位置锁（2026-11-09 事故后加）──
    # 事故：同时开 5 条队列，每条 onnxruntime 按 16 核拉满 → 80 线程，
    # 内存只剩几百 MB，机器卡死、用户被迫重启。
    # 现在：最多允许 SLOTS 条同时跑（默认 2，用户实测 2 条可接受）。
    # 锁用「位置文件」实现：slot1/slot2 各一个 pid 文件，写进去就算占位。
    SLOTS = int(args[args.index("--slots") + 1]) if "--slots" in args else 2
    lampp = ROOT / "_ocr_slots"
    lampp.mkdir(exist_ok=True)
    my_slot = None
    for i in range(1, SLOTS + 1):
        f = lampp / f"slot{i}.pid"
        alive = False
        if f.exists():
            try:
                old = int(f.read_text().strip())
            except Exception:
                old = 0
            alive = _pid_alive(old)
            if alive:
                continue
            print(f"[lock] slot{i} 陈旧 pid={old} 已死，接管", flush=True)
        try:
            f.write_text(str(os.getpid()))
            my_slot = f
            break
        except Exception:
            continue
    if my_slot is None:
        print(f"[locked] 已有 {SLOTS} 条队列在跑，本次不启动。", flush=True)
        print("判定：SKIP（并发限制）")
        return
    if not _mem_ok(1500):
        print("[mem] 可用内存不足 1.5 GB，本次不启动（防卡死）", flush=True)
        print("判定：SKIP（内存守门）")
        try:
            my_slot.unlink()
        except Exception:
            pass
        return
    _pin_high_priority()
    print(f"[lock] 占用 {my_slot.name}  (pid {os.getpid()})", flush=True)
    try:
        _run(lst, dpi, only, threads, pause)
    finally:
        try:
            my_slot.unlink()
        except Exception:
            pass


def _run(lst, dpi, only, threads, pause):
    report = []
    for it in lst:
        if only and only not in it["out"]:
            continue
        dst = layer_dir(it.get("layer", "L2"))
        if dst is None:
            continue
        out = dst / it["out"]
        if out.exists() and out.stat().st_size > 300_000:
            print(f"[skip] {it['book']}", flush=True)
            report.append({"book": it["book"], "status": "skip-exists"})
            continue
        cmd = [PY, str(TOOL), it["pdf"], str(out),
               "--book", it["book"], "--vol", it.get("vol", ""),
               "--source", it.get("source", ""), "--dpi", dpi,
               "--threads", str(threads), "--pause", str(pause)]
        t0 = time.time()
        print(f"[start] {it['book']}  threads={threads} pause={pause}", flush=True)
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            print(f"[FAIL] {it['book']}  {(p.stderr or '')[-200:]}", flush=True)
            report.append({"book": it["book"], "status": "fail",
                           "err": (p.stderr or "")[-300:]})
            continue
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            r = {"raw": p.stdout[-200:]}
        ratio = r.get("measured_ratio", 0)
        grade = "OK" if ratio >= 0.5 else ("LOW" if ratio >= 0.25 else "BAD")
        r.update({"book": it["book"], "status": "ok", "grade": grade,
                  "minutes": round((time.time() - t0) / 60, 1)})
        report.append(r)
        print(f"[{grade}] {it['book']}  pages={r.get('pages')} ratio={ratio} "
              f"{r.get('minutes')} 分", flush=True)

        (ROOT / "_ocr_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 汇总 ===")
    ok = [r for r in report if r.get("status") in ("ok", "skip-exists")]
    low = [r for r in report if r.get("grade") in ("LOW", "BAD")]
    print(f"共 {len(report)}｜完成 {len(ok)}｜低实测率 {len(low)}")
    for r in low:
        print(f"  {r['book']}  {r.get('grade')}  ratio={r.get('measured_ratio')}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ocr_supervisor.py —— OCR 队列看门狗

2026-11-10 建。理由：六本书 4997 页要跑 11 小时，中间进程崩了/机器重启就断了，
没人看着就白等。这个脚本每 60 秒查一次，队列里哪本没在跑就补上。

做法：
  ① 扫描待办清单（R_ocr.json / L3_ocr.json）
  ② 对每本书检查：输出 txt 是否已存在且完整、.ocr.jsonl 有几页
  ③ 若既没完成、又没进程在跑它 → 起一条
  ④ 最多同时 MAX 条队列（默认 2，内存安全线）

用法：python ocr_supervisor.py --once   只检查一次（自己挂定时任务用）
      python ocr_supervisor.py          常驻，每 60 秒一轮
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pymupdf

ML = Path(r"G:\path\to\workspace\马理论输出\_ml")
TOOL = Path(r"G:\path\to\knowledge-base\工具\ocr_pdf_paged.py")
BASE = Path(r"G:\path\to\knowledge-base\学科资料")
MAX = 1        # 2026-11-10 事故后改：同时只跑 1 条（原 2 条导致卡机）
THREADS = 4      # 16 核用 4 核，CPU 目标 70% 左右（6 线程实测 93%，太高）
DPI = 170      # 略降，减少内存峰值（原 180）
MIN_FREE_GB = 3.0   # 内存低于 3 GB 就暂停，防卡死
SLEEP_SEC = 60      # 每轮间隔 60 秒

QUEUES = [ML / "R_ocr.json", ML / "L3_ocr.json"]


def load_jobs():
    jobs = []
    for q in QUEUES:
        if not q.exists():
            continue
        for x in json.loads(q.read_text(encoding="utf-8")):
            jobs.append(x)
    return jobs


def out_txt(job):
    """这本书的最终 txt 落点"""
    d = {"R": "R-辞书与工具书", "L3": "L3-与马哲相邻的理论家",
         "L2": "L2-西方马克思主义", "L1": "L1-中国化马克思主义",
         "L0": "L0-马恩列原著"}.get(job.get("layer", ""), "R-辞书与工具书")
    return BASE / d / job["out"]


def progress(job):
    """返回 (已完成页数, 总页数)"""
    total = 0
    pdf = job.get("pdf", "")
    if os.path.exists(pdf):
        try:
            total = pymupdf.open(pdf).page_count
        except Exception:
            total = 0
    jl = Path(str(out_txt(job)) + ".ocr.jsonl")
    done = 0
    if jl.exists():
        try:
            done = sum(1 for _ in jl.open(encoding="utf-8", errors="replace"))
        except Exception:
            done = 0
    return done, total


def running_books():
    """当前有哪些书正在 OCR。

    修正记录（2026-11-10，第六版）：
      一修：wmic 在 Win11 已被移除 → 静默失败 → 重复起队列
      二修：用 python 源码写 PowerShell，`$_` 的转义写坏 → 命令语法错
      三修：识别失败就返回 None → 空转悖论（重启后永远不干活）
      四修：把「跑失败」和「没结果」分开处理
      五修：仍然重复起队列 —— 根因是 `\\$_` 那个反斜杠**进了 PowerShell 命令**，
            PowerShell 报 `\.CommandLine : 无法识别`，stdout 为空，被判成「没人跑」
      六修（本版）：PowerShell 命令里**一个反斜杠都不写**，
            并用 stderr 非空作为「识别失败」的判据。
    """
    jobs = load_jobs()
    # 注意：这段命令里不能出现任何反斜杠，也不能出现 $_
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object -ExpandProperty CommandLine"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=40,
            encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"    [警告] PowerShell 起不来（{e}），本轮不动", flush=True)
        return None

    # stderr 有内容 = 命令本身有问题，不能当作「没人跑」
    if r.returncode != 0 or (r.stderr or "").strip():
        print(f"    [警告] PowerShell 出错 rc={r.returncode} "
              f"{(r.stderr or '')[:60]}，本轮不动", flush=True)
        return None

    lines = [x for x in r.stdout.splitlines() if "ocr_pdf_paged" in x]
    out = set()
    for j in jobs:
        stem = Path(j.get("pdf", "")).stem
        if stem and any(stem[:14] in ln for ln in lines):
            out.add(j["book"])
    print(f"    [识别] 在跑 {len(lines)} 个 OCR 进程，匹配到 {len(out)} 本书", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    while True:
        jobs = load_jobs()
        running = running_books()
        if running is None:
            if args.once:
                return
            time.sleep(SLEEP_SEC)
            continue
        need = []
        for j in jobs:
            done, total = progress(j)
            txt = out_txt(j)
            if total and done >= total and txt.exists():
                continue                     # 已完成
            if j["book"] in running:
                continue                     # 正在跑
            # jsonl 已有但没 txt：不需要 OCR，只需要转换
            if total and done >= total and not txt.exists():
                continue                     # 让 convert_pending_ocr 处理
            need.append((j, done, total))

        slots = MAX - len(running)
        print(f"[{time.strftime('%H:%M:%S')}] 在跑 {len(running)} 条，"
              f"待办 {len(need)} 本，空位 {slots}", flush=True)
        for j, done, total in need:
            print(f"        {j['book'][:28]}  {done}/{total}", flush=True)

        for j, done, total in need[:max(0, slots)]:
            # 内存门槛：低于 MIN_FREE_GB 就不启，宁可慢也不能卡
            try:
                import ctypes

                class _MS(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong),
                                ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong),
                                ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong),
                                ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong),
                                ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

                _ms = _MS()
                _ms.dwLength = ctypes.sizeof(_MS)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(_ms))
                free_gb = _ms.ullAvailPhys / (1024 ** 3)
            except Exception:
                free_gb = 99.0
            if free_gb < MIN_FREE_GB:
                print(f"    [跳过] 内存只剩 {free_gb:.1f} GB，本轮不启动", flush=True)
                continue
            cmd = [sys.executable, str(TOOL), j["pdf"], str(out_txt(j)),
                   "--threads", str(THREADS), "--dpi", str(DPI)]
            print(f"    启动 {j['book'][:26]}", flush=True)
            # 2026-11-10 修：原来 stdout/stderr 全 DEVNULL，进程秒退时看不到原因。
            #   改写成日志文件，出错能查。
            _logdir = Path(r"G:\path\to\knowledge-base\工具\_ocr_logs")
            _logdir.mkdir(exist_ok=True)
            _log = open(_logdir / (j["book"][:20].replace("/", "_") + ".log"),
                        "a", encoding="utf-8", errors="replace")
            subprocess.Popen(cmd, cwd=str(TOOL.parent),
                             stdout=_log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)

        if args.once:
            return
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    main()

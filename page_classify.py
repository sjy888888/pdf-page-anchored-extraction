# -*- coding: utf-8 -*-
"""page_classify.py —— 把「不可引用的页」和「乱码页」分开

2026-11-10 两版判据的差别：

  第一版 noise_audit.py 判据过宽：
    凡是「中文占比 < 55%」就算噪音。结果把**目录页、版权页、扉页**
    全部算进去了（《选集》第4卷 29.7%「噪音」）。

  但这两类的处置完全不同：
    目录页 —— 内容可读，只是不是正文。**不能引，但不必修。**
    真乱码 —— OCR 严重错误（`皿.马恩著作-文集凹.All` / `1∞706` / `λ"`），
              **检索命中会返回乱码当原文**，这才是要处理的。

  实测证据：`选集04-第3版` 的 pdf3 是目录页，
  点线 `........` 被抽成了 `!!!!!!!!!!!!`（可读，不是乱码）；
  而 `文集05` 的 pdf4 是版权页乱码（不可读）。

分类：
  TOC   目录页 —— 有序号+页码区间，或含大量点线
  FRONT 前置页（版权/CIP/扉页/编委会）—— 含 ISBN/CIP/出版发行/编委
  GARBLE 真乱码 —— 中文占比 < 40% 且没有 TOC/FRONT 特征
  BODY  正文
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"G:\path\to\knowledge-base\学科资料")
SEP = re.compile(r"^={10,}\s*$", re.M)
PAGE = re.compile(r"^【p[^\]]*】.*$", re.M)
CJK = re.compile(r"[\u4e00-\u9fff]")
DOTS = re.compile(r"[.．·…!！]{4,}")
PNUM = re.compile(r"\d{1,4}\s*[—\-–]\s*\d{1,4}")
FRONT = re.compile(r"ISBN|CIP|出版发行|编委|版权所有|图书在版|定价|开本|印张|字数|书号")


def page_blocks(text):
    marks = list(PAGE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        tag = re.match(r"【([^】]*)】", m.group(0))
        out.append((tag.group(1) if tag else "?", text[m.end():end]))
    return out


def classify(body):
    if not body.strip():
        return "EMPTY"
    total = sum(len(l.strip()) for l in body.splitlines() if l.strip())
    if total == 0:
        return "EMPTY"
    cjk = len(CJK.findall(body))
    ratio = cjk / total
    if FRONT.search(body) and ratio > 0.25:
        return "FRONT"
    # 目录：≥2 处「数字—数字」页码区间，或点线 ≥3 处
    if len(PNUM.findall(body)) >= 2 or len(DOTS.findall(body)) >= 3:
        return "TOC"
    if ratio < 0.40:
        return "GARBLE"
    return "BODY"


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "L0"
    dirs = [d for d in ROOT.iterdir() if d.is_dir() and d.name.startswith(target + "-")]
    agg = {"BODY": 0, "TOC": 0, "FRONT": 0, "GARBLE": 0, "EMPTY": 0}
    garble_files = []
    for d in dirs:
        for p in sorted(d.rglob("*.txt")):
            if "_他人评述" in str(p):
                continue
            t = p.read_text(encoding="utf-8", errors="replace")
            m = list(SEP.finditer(t))
            body = t[m[1].end():] if len(m) >= 2 else t
            blocks = page_blocks(body)
            if not blocks:
                continue
            cnt = {"BODY": 0, "TOC": 0, "FRONT": 0, "GARBLE": 0, "EMPTY": 0}
            gpages = []
            for tag, b in blocks:
                c = classify(b)
                cnt[c] += 1
                if c == "GARBLE":
                    gpages.append(tag)
            for k in agg:
                agg[k] += cnt[k]
            if gpages:
                garble_files.append((p.name, len(blocks), len(gpages), gpages[:8]))

    tot = sum(agg.values())
    print(f"{target} 共 {tot} 页")
    for k in ["BODY", "TOC", "FRONT", "GARBLE", "EMPTY"]:
        print(f"  {k:<7} {agg[k]:>6}  {agg[k]/tot*100:>5.1f}%")
    print(f"\n有真乱码的文件 {len(garble_files)} 个：")
    for n, t_, g, pages in garble_files:
        print(f"  {n[:44]:<46} {g:>4}/{t_:<5} 乱码页 {', '.join(pages)}")


if __name__ == "__main__":
    main()

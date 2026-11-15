# -*- coding: utf-8 -*-
"""page_report.py —— 生成全库页码率逐卷台账（Markdown）

2026-11-10 用户提醒：**「不是所有卷都有 91% 的页码实测率」**。
我上一轮报「L0 98.2% 可直接引」是平均数，掩盖了第33卷只有 42.3% 的事实。

这个脚本出一张**逐卷台账**，每卷一行，按实测率升序——一眼看出哪些卷不能盲引。

分级（引用时按这个判断）：
  A  ≥90%  可直接引，页码稳
  B  70–90% 大体可引，个别页需核
  C  50–70% 需挑页，引前查上下文
  D  <50%   只当检索线索，页码基本不可引
  N       无页标记（网页源）
"""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(r"G:\path\to\knowledge-base\学科资料")
SEP = re.compile(r"^={10,}\s*$", re.M)
MEAS = re.compile(r"^【p\d+", re.M)
UNK = re.compile(r"^【p\?", re.M)


def grade(r):
    if r >= 0.90:
        return "A"
    if r >= 0.70:
        return "B"
    if r >= 0.50:
        return "C"
    return "D"


def main():
    out = []
    out.append(f"# 全库页码率台账（{date.today()}）\n")
    out.append("> 用户提醒：「不是所有卷都有 91% 的页码实测率」。这张表按实测率**升序**排，")
    out.append("> 引用前先看这一卷在哪个档。A 可直接引；D 只能当检索线索。\n")
    out.append("| 档 | 实测率 | 卷/文件 | pN | p? |")
    out.append("|---|---:|---|---:|---:|")

    rows = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or not re.match(r"^[LRXT]\d*-", d.name):
            continue
        for p in sorted(d.rglob("*.txt")):
            if "_他人评述" in str(p) or "_机构动态" in str(p):
                continue
            t = p.read_text(encoding="utf-8", errors="replace")
            m = list(SEP.finditer(t))
            body = t[m[1].end():] if len(m) >= 2 else t
            a, u = len(MEAS.findall(body)), len(UNK.findall(body))
            tot = a + u
            if tot == 0:
                rows.append((None, d.name, p.name[:-4], a, u))
            else:
                rows.append((a / tot, d.name, p.name[:-4], a, u))

    # 升序：无页标记的排最前（N），然后按比率升序
    rows.sort(key=lambda x: (x[0] is not None, x[0] if x[0] is not None else 0))

    cnt = {"A": 0, "B": 0, "C": 0, "D": 0, "N": 0, "W": 0}
    for r, layer, name, a, u in rows:
        if r is None:
            g, rs = "N", "—"
            cnt["N"] += 1
        elif tot <= 2 and a == 0:
            # 网页源：整篇只有 1-2 个【p?】标记，本来就没有印刷页码。
            # 第一版把它算成 0.0% D 档，和「扫描件页码缺失」混为一谈，
            # 结果 160 篇网页文章全落 D 档，台账失去意义。单列 W 档。
            g, rs = "W", "网页源"
            cnt["W"] += 1
        else:
            g, rs = grade(r), f"{r*100:.1f}%"
            cnt[g] += 1
        out.append(f"| {g} | {rs} | {layer}/{name} | {a} | {u} |")

    out.append("")
    out.append("## 汇总\n")
    tot = sum(cnt.values())
    out.append(f"- 共 {tot} 个文件")
    for g in ["A", "B", "C", "D", "N", "W"]:
        out.append(f"- **{g}**: {cnt[g]}")
    out.append("")
    out.append("**引用纪律**：")
    out.append("- A/B 档：`【原文】……（《书名》p123）`")
    out.append("- C 档：引前查该页上下文，确认是正文再引")
    out.append("- D 档：只写 `【待核对】`，不给页码")
    out.append("- N 档（网页源）：`【原文】……（页码待核）`")

    dst = ROOT.parent / "事实" / f"{date.today()}-全库页码率台账.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")
    print(f"已写 {dst}")
    print(f"A {cnt['A']} | B {cnt['B']} | C {cnt['C']} | D {cnt['D']} | N {cnt['N']}")


if __name__ == "__main__":
    main()

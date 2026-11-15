# -*- coding: utf-8 -*-
"""page_audit.py —— 全库页码标定审计（按覆盖率分级，不按「全或不全」）

「六步」里的「页码标定」做到什么程度，得有一张表。

**第一版判据是错的**：把「全 pN」才算 MEASURED，其余算 PARTIAL。
结果 L0 那 49 卷明明有大量真页码，全被判 PARTIAL、全页码率 0.0%。
《列宁全集》第 31 卷实测：144 页里 129 页有印页码、15 页没有
（书末索引页、插图页本来就不印页码）——这是**正常的好数据**，不是「部分」。

第二版判据：按覆盖率分四档
  A ≥90%   可直接引，页码极稳
  B 50–90% 可直引，个别页要标「页码待核」
  C 1–50%  有页码但缺得多，引用时要核
  D 0%     网页源/OCR 未成功，一律「页码待核」
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"G:\path\to\knowledge-base\学科资料")
MEAS = re.compile(r"^【p\d+", re.M)
UNK = re.compile(r"^【p\?", re.M)
SEP = re.compile(r"^={10,}\s*$", re.M)


def body_only(text):
    """跳过文件头的元数据块，只留正文

    文件头里有说明文字 `【pN】= 从该页页眉…`，其字面 `【pN】`/`【p?`
    会被正则当成真实页标记，把每个文件都污染成「既有 pN 又有 p?」。
    """
    marks = list(SEP.finditer(text))
    if len(marks) >= 2:
        return text[marks[1].end():]
    return text


def grade(text):
    """返回 (等级, 实测页数, 无页码页数)"""
    b = body_only(text)
    m, u = len(MEAS.findall(b)), len(UNK.findall(b))
    tot = m + u
    if tot == 0:
        return "D", 0, 0          # 一个页标记都没有（网页源）
    r = m / tot
    if r >= 0.90:
        return "A", m, u
    if r >= 0.50:
        return "B", m, u
    if r > 0:
        return "C", m, u
    return "D", m, u


def main():
    layers = sys.argv[1:] or ["L0", "L1", "L2", "L3", "L4", "L5"]
    grand = {"A": 0, "B": 0, "C": 0, "D": 0}
    for lay in layers:
        dirs = [d for d in ROOT.iterdir() if d.is_dir() and d.name.startswith(lay + "-")]
        if not dirs:
            continue
        cnt = {"A": 0, "B": 0, "C": 0, "D": 0}
        files = 0
        for d in dirs:
            for p in d.rglob("*.txt"):
                if "_他人评述" in str(p) or p.name.endswith(".pages.json"):
                    continue
                try:
                    t = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                files += 1
                g, _m, _u = grade(t)
                cnt[g] += 1
        for k in cnt:
            grand[k] += cnt[k]
        usable = (cnt["A"] + cnt["B"]) / files * 100 if files else 0
        print(f"{lay}  {files:>4} 文件 | A>=90% {cnt['A']:>4} | B 50-90% {cnt['B']:>4} | "
              f"C 1-50% {cnt['C']:>3} | D 无页码 {cnt['D']:>4} | 可直接引 {usable:5.1f}%")
    tot = sum(grand.values())
    print("-" * 92)
    usable = (grand["A"] + grand["B"]) / tot * 100 if tot else 0
    print(f"全库 {tot:>4} 文件 | A {grand['A']:>4} | B {grand['B']:>4} | "
          f"C {grand['C']:>3} | D {grand['D']:>4} | 可直接引 {usable:5.1f}%")


if __name__ == "__main__":
    main()

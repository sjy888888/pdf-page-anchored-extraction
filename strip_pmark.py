# -*- coding: utf-8 -*-
"""strip_pmark.py —— 清掉网页版文件里的伪页码标记

2026-11-10 用户：「如果没有页码，就不标了，宁无不假，哪怕让我自己核。」

现状：一批从网页抓来的单篇（毛泽东单篇、习近平讲话等），
文件里标了一个孤零零的 `【p?】`——那不是页码，是占位垃圾。
它对检索没好处，对引用有害（看着像页码）。

本脚本做的事：
  ① 找出「全文只有 1 个【p…】标记 且 是 p?」的文件 → 判定为网页版
  ② 删掉那个标记行
  ③ 在文件头加一句明确声明：本文件无印刷页码，引用请查纸本
  ④ 备份原文件
"""
import re
import shutil
from datetime import date
from pathlib import Path

BASE = Path(r"G:\path\to\knowledge-base\学科资料")
LAYERS = ("L0-", "L1-", "L2-", "L3-", "L4-", "L5-", "T-", "R-", "X-")
BAK = BASE / "_pmark备份"

MARK = re.compile(r"^【p\?[^\]]*】\s*$", re.M)
ANY = re.compile(r"^【p", re.M)


def main():
    targets = []
    for d in BASE.iterdir():
        if not d.is_dir() or not d.name.startswith(LAYERS):
            continue
        for f in d.rglob("*.txt"):
            if any(p.startswith("_") for p in f.relative_to(BASE).parts):
                continue
            try:
                t = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            marks = ANY.findall(t)
            # 只有 1 个标记，且是 p? → 网页版占位
            if len(marks) != 1:
                continue
            m = MARK.search(t)
            if not m:
                continue
            targets.append((f, t, m))

    print(f"找到 {len(targets)} 个「只有一个 p? 占位」的网页版文件\n")
    for f, t, m in targets[:20]:
        print(f"  {f.relative_to(BASE)}")

    if not targets:
        return

    BAK.mkdir(parents=True, exist_ok=True)
    n = 0
    for f, t, m in targets:
        shutil.copy2(f, BAK / f.name)
        # 删掉标记行
        t2 = MARK.sub("", t, count=1)
        # 在文件头加声明（如果还没有）
        decl = ("★ 页码说明：本文件来自网页/单行本，**无印刷页码**。\n"
                "  引用时不写页码，或自行查纸本核对。**不要给它编一个页码。**\n")
        if "无印刷页码" not in t2[:1500]:
            sep = re.search(r"^={20,}\s*$", t2, re.M)
            if sep:
                # 插在第一个分隔线之前
                t2 = t2[:sep.start()] + decl + t2[sep.start():]
        # 压实多余空行
        t2 = re.sub(r"\n{3,}", "\n\n", t2)
        f.write_text(t2, encoding="utf-8")
        n += 1

    print(f"\n已处理 {n} 个，备份在 {BAK}")


if __name__ == "__main__":
    main()

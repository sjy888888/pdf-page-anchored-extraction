# -*- coding: utf-8 -*-
"""
fix_punct_map.py —— 修复「标点被字体映射成 ASCII 符号」的抽取结果

问题（2026-11-09，《毛泽东著作专题摘编》实测）：
  该 PDF 用了一个自定义字体 `DY38+ZLbBrz-39`，标点全被映射成 ASCII：
      原始:  中!才能触到那种现象!也只有在亲身参加变革"世上最可笑的是那些#知识里手$!
      应还原: 中，才能触到那种现象，也只有在亲身参加变革。世上最可笑的是那些"知识里手"，
  **汉字没坏，只有标点坏了。** 所以不是 OCR 问题，是映射表问题。

怎么定的映射（不是猜的，是从上下文反推 + 全库频率校验）：
  取 100 个含该符号的片段，逐条看它在句中的位置：
    ! 出现在分句之间且前后都不是引号 → ，
    " 出现在句末或并列句末 → 。
    # 后接名词、且该名词后又出现 $ → "（开引号）
    $ 与 # 配对，出现在名词后 → "（闭引号）
    % 后接书名、& 在其后 → 《
    & 与 % 配对 → 》
    ’ 后接年份或人名 → （
    ) 后接年代词或单位 → ）
  频率校验：还原后全库 `，` 出现次数应约为 `。` 的 2–3 倍（中文常态）；
  若映射错了，这个比例会明显偏离。实测 2.41，在正常区间。

用法:
  python fix_punct_map.py <in.txt> [--out out.txt]
  默认原地改写，改前存 .bak-punctmap
"""
import sys
import shutil
from pathlib import Path
from collections import Counter

# 映射表。—— 顺序要紧：`《》` 必须比 `""` 先换，否则嵌套书名号会把引号吃掉。
MAP = [
    ("%", "《"),
    ("&", "》"),
    ("#", "“"),
    ("$", "”"),
    ("’", "（"),
    ("(", "）"),     # 注意：只有在「全是这种字体」时才安全，见下
    ("!", "，"),
    ('"', "。"),
    ("-- ", "——"),
]


def analyze(text):
    """统计待还原符号，并给出「还原后逗号/句号比」作为合理性判据"""
    c = Counter(ch for ch in text if ch in "!\"#$%&’()")
    comma = text.count("!") + text.count("，")
    period = text.count('"') + text.count("。")
    return c, comma, period, (comma / period if period else 0)


def fix(text):
    out = text
    for a, b in MAP:
        out = out.replace(a, b)
    return out


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src
    text = src.read_text(encoding="utf-8")

    c, comma, period, ratio = analyze(text)
    print("待还原符号分布：", dict(c))
    print(f"还原后 逗号/句号 = {comma}/{period} = {ratio:.2f}（中文常态 1.8–3.2）")

    if ratio < 1.0 or ratio > 5.0:
        print("⚠ 比例偏离常态，映射可能不对。停止改写。")
        sys.exit(1)

    if dst == src:
        bak = src.with_suffix(src.suffix + ".bak-punctmap")
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f"备份 → {bak.name}")

    fixed = fix(text)
    # 还原后不该再出现这几个符号（除非原文真有，极少）
    leftover = counter_left = Counter(ch for ch in fixed if ch in "!\"#$%&")
    dst.write_text(fixed, encoding="utf-8")
    print(f"写出 → {dst.name}  {len(fixed):,} 字")
    print("残留符号：", dict(leftover) or "无")


if __name__ == "__main__":
    main()

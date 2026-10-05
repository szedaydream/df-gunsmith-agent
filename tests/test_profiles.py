# -*- coding: utf-8 -*-
"""档案库单测：加载、命中、别名、格式化"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.gun_profiles import find_profiles, format_for_prompt

hits = find_profiles("K437 烽火地带怎么改，顺便问问 mk47")
assert set(hits) == {"K437", "MK47"}, f"命中不对: {hits.keys()}"
print("✅ 命中与别名正常:", list(hits.keys()))

text = format_for_prompt(hits)
assert "可改装槽位" in text and "弹匣座" in text, "槽位格式化缺失"
assert "1.25 倍" in text, "特性缺失"
assert "待核对" in text, "未核对标记缺失"
print("✅ 格式化输出正常:")
print(text)

empty = find_profiles("今天天气怎么样")
assert empty == {}, "不该命中的命中了"
print("\n✅ 无关文本不误命中")

# 边界回归：m4 不该命中 M4A1 之外的东西，mk4 和 mk47 互不串
assert set(find_profiles("m4 怎么改")) == {"M4A1"}
assert set(find_profiles("mk4 三连发")) == {"MK4"}
print("✅ 词边界匹配正常（mk4/mk47/m4 互不串扰）")

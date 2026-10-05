# -*- coding: utf-8 -*-
"""质检器单测：结构/槽位/标注/红线四个检查项，好的放行坏的必抓"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.plan_validator import validate_plan, build_fix_instruction

GOOD = """【枪械】K437（共 9 个配件槽）
【定位】烽火地带 + 中近距离
【配件方案】
- 枪口：一体消音器 —— 隐蔽（聪聪）
- 枪管：长枪管 —— 射程（未核实）
- 枪托：骨架枪托 —— 稳定（推理）
- 上左右导轨：镭射 —— 腰射（推理）
- 前握把：垂直握把 —— 压垂直（NGA）
- 后握把：镂空握把 —— 操控（推理）
- 弹匣座：快拔套 —— 换弹（聪聪）
- 瞄具：红点 —— 视野（聪聪）
- 副瞄具：（空）—— 中近用不上（推理）
【精校建议】枪口拉后坐力（推理）
【属性评估】操控偏低，能接受吗
【使用建议】先手打头"""

PROFILES = {"K437": {"slots": ["枪口", "枪管", "枪托", "上左右导轨", "前握把",
                               "后握把", "弹匣座", "瞄具", "副瞄具"]}}

issues = validate_plan(GOOD, PROFILES)
assert issues == [], f"合格方案不该有问题: {issues}"
print("✅ 合格方案放行")

# 缺段
bad = GOOD.replace("【精校建议】枪口拉后坐力（推理）\n", "")
issues = validate_plan(bad, PROFILES)
assert any("精校建议" in i for i in issues), f"缺段没抓到: {issues}"
print("✅ 缺【精校建议】段被抓:", issues)

# 配件无标注
bad = GOOD.replace("（聪聪）\n- 枪管", "\n- 枪管", 1)
issues = validate_plan(bad, PROFILES)
assert any("来源标注" in i for i in issues), f"无标注没抓到: {issues}"
print("✅ 配件缺来源标注被抓")

# 槽位声称与列出条数不符
bad = GOOD.replace("（共 9 个配件槽）", "（共 8 个配件槽）")
issues = validate_plan(bad, PROFILES)
assert any("声称共 8" in i for i in issues) and any("档案不符" in i for i in issues), \
    f"槽位问题没抓全: {issues}"
print("✅ 槽位自洽+对档案双抓")

# 红线覆盖：档案填了 red_lines 就要逐项提到
p2 = {"K437": {**PROFILES["K437"], "red_lines": {"操控速度": "低于 35 近战吃亏"}}}
issues = validate_plan(GOOD, p2)   # GOOD 的属性评估只说"操控偏低"，没有「操控速度」四字
assert any("操控速度" in i for i in issues), f"红线没抓到: {issues}"
issues = validate_plan(GOOD.replace("操控偏低", "操控速度偏低"), p2)
assert issues == [], f"提了红线还报错: {issues}"
print("✅ 红线覆盖检查正常（red_lines 填了才激活）")

# 非方案类直接放行
assert validate_plan("枪口补偿器和消音器的区别是……", PROFILES) == []
# 多枪对比场景跳过档案槽位核对（数量没法对单枪）
two_guns = {**PROFILES, "M4A1": {"slots": ["枪口"] * 12}}
assert validate_plan(GOOD, two_guns) == [], f"多枪场景误伤: {validate_plan(GOOD, two_guns)}"
print("✅ 非方案类/多枪场景不误伤")

# 修正指令
fix = build_fix_instruction(["缺【精校建议】段", "配件缺来源标注：枪口"])
assert "1." in fix and "2." in fix and "重新输出完整方案卡" in fix
print("✅ 修正指令生成正常")

print("\n全部通过")

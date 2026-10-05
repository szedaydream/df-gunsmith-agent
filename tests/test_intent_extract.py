# -*- coding: utf-8 -*-
"""回归测试：意图判断升级为提取器（管道键值对解析 + 提取正确性）"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from services.ai_service import AIService

# --- 1. 解析器单测（不调 API）：正常行、坏行、缺字段行 ---
print("=== 解析器单测 ===")
p = AIService._parse_intent
r = p("ANSWER|枪=SVCH|模式=全面战场|距离=远|指定配件=5倍镜")
assert r["action"] == "answer" and r["slots"]["枪"] == "SVCH" \
    and r["slots"]["指定配件"] == "5倍镜", f"正常行解析错: {r}"
r = p("CLARIFY|枪=M4A1|模式=未知|距离=未知|指定配件=无")
assert r["action"] == "clarify" and r["slots"]["模式"] == "未知", f"clarify 行错: {r}"
r = p("CHAT|枪=无|模式=无|距离=无|指定配件=无")
assert r["action"] == "chat", f"chat 行错: {r}"
r = p("ANSWER")                      # 字段全丢：意图不能丢
assert r["action"] == "answer" and r["slots"] == {}, f"裸标签错: {r}"
r = p("乱七八糟的回复")               # 完全异常：降级 answer
assert r["action"] == "answer", f"异常行错: {r}"
print("✅ 解析器 5 种形态全部正确")

# --- 2. 真实调用：指定配件场景（对应测试集 F3）---
print("\n=== 真实调用：SVCH 指定 5 倍镜 ===")
ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model=Config.LLM_MODEL, use_builtin_search=True,
               router_api_key=Config.ROUTER_API_KEY,
               router_base_url=Config.ROUTER_BASE_URL,
               router_model=Config.ROUTER_MODEL)
print(f"（router 模型：{Config.ROUTER_MODEL}）")
intent = ai.judge_intent([{"role": "user", "content": "SVCH 我想装 5 倍镜打远点，全面战场"}])
print(f"结果: {intent}")
assert intent["action"] == "answer", f"❌ 应判 answer: {intent}"
s = intent["slots"]
assert "SVCH" in s.get("枪", "").upper().replace(" ", ""), f"❌ 枪名提取错: {s}"
assert "全面战场" in s.get("模式", ""), f"❌ 模式提取错: {s}"
assert "5" in s.get("指定配件", "") and "倍镜" in s.get("指定配件", ""), f"❌ 指定配件提取错: {s}"
print("✅ 枪/模式/指定配件提取正确")

# --- 3. 真实调用：信息不全仍判 clarify，但枪名要提出来 ---
print("\n=== 真实调用：信息不全 ===")
intent2 = ai.judge_intent([{"role": "user", "content": "帮我改把 M4A1"}])
print(f"结果: {intent2}")
assert intent2["action"] == "clarify", f"❌ 回退！信息不全却没判 clarify: {intent2}"
assert "M4A1" in intent2["slots"].get("枪", "").upper().replace(" ", ""), \
    f"❌ clarify 也该提出枪名: {intent2['slots']}"
print("✅ clarify 判定与枪名提取正常")

# --- 4. 真实调用：历史里说过模式/距离，不重复反问且字段从历史补全 ---
print("\n=== 真实调用：历史补全 ===")
history = [
    {"role": "user", "content": "帮我改把 M4A1，全面战场打中远距离"},
    {"role": "assistant", "content": "【枪械】M4A1\n【定位】全面战场 + 中远距离\n【配件方案】……（略）"},
    {"role": "user", "content": "那 AKM 呢，也给我来一套"},
]
intent3 = ai.judge_intent(history)
print(f"结果: {intent3}")
assert intent3["action"] == "answer", f"❌ 回退！重复反问: {intent3}"
assert "AKM" in intent3["slots"].get("枪", "").upper(), f"❌ 新枪名提取错: {intent3['slots']}"
assert "全面战场" in intent3["slots"].get("模式", ""), f"❌ 历史模式没补全: {intent3['slots']}"
print("✅ 不重复反问，模式从历史补全")

# --- 5. 形容词识别：取向/水平/预算 ---
print("\n=== 形容词识别：取向/水平/预算 ===")
cases5 = [
    ("预算有限，MP5 烽火地带近战怎么配", {"取向": "性价比"}),
    ("我是新手刚入坑，M4A1 烽火地带近战怎么改，30万以内", {"水平": "新手", "预算": "30"}),
    ("满改不差钱，给我整把猛攻的 K416 打全面战场近战", {"取向": None}),  # 取向应含 满改/激进 之一
]
for q, expect in cases5:
    r = ai.judge_intent([{"role": "user", "content": q}])
    s = r["slots"]
    print(f"  {q[:18]}… → {s}")
    for k, v in expect.items():
        if v is not None:
            assert v in s.get(k, ""), f"❌ {q[:12]}… 字段{k}应含「{v}」: {s}"
    if "满改不差钱" in q:
        assert any(w in s.get("取向", "") for w in ("满改", "激进")), f"❌ 取向识别错: {s}"
print("✅ 取向/水平/预算提取正常")

print("\n全部通过")

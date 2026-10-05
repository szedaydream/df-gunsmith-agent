# -*- coding: utf-8 -*-
"""回归测试：k2.5 可用性探测 + 意图判断上下文修复验证"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from config import Config
from services.ai_service import AIService

client = OpenAI(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL)

# --- 1. kimi-k2.5 探测：发一句你好，看通不通、多快 ---
import time
print("=== 探测 kimi-k2.5 ===")
try:
    t0 = time.time()
    resp = client.chat.completions.create(
        model="kimi-k2.5",
        messages=[{"role": "user", "content": "你好，回复一个字即可"}],
        max_tokens=2000,
    )
    dt = time.time() - t0
    print(f"✅ kimi-k2.5 可用，耗时 {dt:.1f}s，回复: {resp.choices[0].message.content!r}")
except Exception as e:
    print(f"❌ kimi-k2.5 不可用: {e}")

# --- 2. 意图判断：历史里说过模式/距离，最新一句没提，应判 answer 而非 clarify ---
print("\n=== 意图判断回归：历史已含模式/距离 ===")
ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model=Config.LLM_MODEL, use_builtin_search=True)
history = [
    {"role": "user", "content": "帮我改把 M4A1，全面战场打中远距离"},
    {"role": "assistant", "content": "【枪械】M4A1\n【定位】全面战场 + 中远距离\n【配件方案】……（略）"},
    {"role": "user", "content": "那 AKM 呢，也给我来一套"},
]
intent = ai.judge_intent(history)
print(f"结果: {intent}")
assert intent["action"] == "answer", "❌ 回退！历史里说过模式/距离还判成了 clarify"
print("✅ 判为 answer，没有重复反问")

# --- 3. 对照组：真的没说模式/距离，应判 clarify ---
print("\n=== 意图判断对照：信息确实不全 ===")
intent2 = ai.judge_intent([{"role": "user", "content": "帮我改把 M4A1"}])
print(f"结果: {intent2}")
assert intent2["action"] == "clarify", "❌ 回退！信息不全却没判 clarify"
print("✅ 判为 clarify")

print("\n全部通过")

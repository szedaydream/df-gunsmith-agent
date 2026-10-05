# -*- coding: utf-8 -*-
"""kimi-k2.5 全流程冒烟：内置搜索 + 流式，计时 + 检查来源标注"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from services.ai_service import AIService

ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model="kimi-k2.5", use_builtin_search=True)

messages = [{"role": "user", "content": "K437 烽火地带中近距离怎么改"}]

t0 = time.time()
answer = []
searched = False
for ev in ai.get_chat_response_stream(messages, search_results=None, require_search=True):
    if ev["type"] == "status":
        print(f"[{time.time()-t0:5.1f}s] 状态: {ev['text']}")
        if "资料到手" in ev["text"]:
            searched = True
    elif ev["type"] == "answer":
        answer.append(ev["delta"])
    elif ev["type"] == "error":
        print(f"❌ {ev['text']}")
        break

text = "".join(answer)
print(f"\n[{time.time()-t0:5.1f}s] 生成完毕，正文 {len(text)} 字，搜索触发: {searched}")
print("=" * 60)
print(text[:2000])
print("=" * 60)
has_plan = "【配件方案】" in text
unverified_only = text.count("（未核实）") > 0 and "（聪聪）" not in text and "（NGA）" not in text and "（推理）" not in text
print(f"有方案卡: {has_plan} | 全标未核实: {unverified_only}")

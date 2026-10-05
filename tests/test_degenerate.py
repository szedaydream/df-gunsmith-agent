# -*- coding: utf-8 -*-
"""复读退化修复回归：检测器单测 + 现场复现（逼模型搜不存在的数据）"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from services.ai_service import AIService

# --- 1. 检测器单测 ---
d = AIService._is_degenerate
assert not d(""), "短文本不误判"
junk = "让我再搜索一下具体的槽位信息。" * 30
assert d(junk), "复读没检测出来"
normal = ("M4A1 是一把全能步枪。它射速高。它稳定性好。配件槽位多。"
          "适合中远距离。萌新友好。弹药便宜。市场保有量大。改装成熟。"
          "精校空间大。综合推荐度高。")
assert not d(normal), f"正常文本被误判: {normal[:30]}"
print("✅ 检测器单测通过（复读检出 / 正常文本不误判 / 短文本跳过）")

# --- 2. 现场复现：上次让模型空转的同款 prompt ---
print("\n=== 现场复现：逼模型搜不存在的硬数据 ===")
ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model=Config.LLM_MODEL, use_builtin_search=True)
nasty = ("请联网搜索游戏「三角洲行动」中 PTR-32 这把枪的可改装槽位（配件槽）清单。"
         "必须调用搜索工具，只回答槽位清单。搜不到就明说「没查到」，不要凭印象编。")
try:
    ans = ai._call([{"role": "user", "content": nasty}],
                   max_tokens=2000, use_search_tool=True)
    repeat = ans.count("让我再搜索")
    print(f"返回 {len(ans)} 字，含「让我再搜索」{repeat} 次")
    print("前 300 字:", ans[:300])
    assert repeat < 3, "❌ 回退！仍在复读空转"
    print("✅ 不再空转：要么给出清单，要么明说没查到")
except RuntimeError as e:
    # 复读被检测出来抛异常也是合格行为（调用方会走降级）
    print(f"✅ 复读被检测器拦截（抛 RuntimeError 走降级）: {e}")

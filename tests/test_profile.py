# -*- coding: utf-8 -*-
"""
用户画像（⑤ slots 结构化沉淀）验证：
1) 存储合并规则：空值不覆盖、新值盖旧值、删除生效
2) 提取规则收紧：询问不提取（不污染画像），明确陈述才提取
3) 画像参与意图判断：画像里的模式补缺，不重复反问
4) 端到端：陈述→画像落库→meta 带回→/clear 后仍在→忘记我删除
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from config import Config
from services import StorageService, AIService

# --- 1. 存储合并规则（不调 API，用临时库）---
print("=== 存储合并规则 ===")
import tempfile
s = StorageService(tempfile.mkdtemp())
s.upsert_profile("u1", {"水平": "新手", "取向": "性价比"})
assert s.get_profile("u1") == {"水平": "新手", "取向": "性价比"}
s.upsert_profile("u1", {"预算": "30万", "取向": "无", "水平": ""})   # 空值/无 不覆盖
assert s.get_profile("u1") == {"水平": "新手", "取向": "性价比", "预算": "30万"}, \
    f"空值覆盖了画像: {s.get_profile('u1')}"
s.upsert_profile("u1", {"取向": "满改"})                              # 新值盖旧值
assert s.get_profile("u1")["取向"] == "满改"
s.delete_profile("u1")
assert s.get_profile("u1") == {}
print("✅ 空值不覆盖 / 新值盖旧值 / 删除生效")

# --- 2 & 3. 真实 router 调用：提取收紧 + 画像补缺 ---
print("\n=== 提取收紧（真实 router 调用）===")
ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model=Config.LLM_MODEL, use_builtin_search=True,
               router_api_key=Config.ROUTER_API_KEY,
               router_base_url=Config.ROUTER_BASE_URL,
               router_model=Config.ROUTER_MODEL)

r = ai.judge_intent([{"role": "user", "content": "长枪管多少钱？新手用合适吗"}])
sl = r["slots"]
print(f"  询问场景 → {sl}")
assert sl.get("取向", "无") in ("无", "") and sl.get("水平", "未知") in ("未知", "") \
    and sl.get("预算", "无") in ("无", ""), f"❌ 询问被当成陈述提取了: {sl}"
print("✅ 询问不提取（画像不会被推断值污染）")

r = ai.judge_intent([{"role": "user", "content": "我是新手，预算 30 万，M4A1 烽火地带近战怎么改"}])
sl = r["slots"]
print(f"  陈述场景 → {sl}")
assert "新手" in sl.get("水平", ""), f"❌ 明确陈述的水平没提取: {sl}"
assert "30" in sl.get("预算", ""), f"❌ 明确陈述的预算没提取: {sl}"
print("✅ 明确陈述正常提取")

print("\n=== 画像补缺（真实 router 调用）===")
r = ai.judge_intent([{"role": "user", "content": "帮我改把 M4A1，打近战"}],
                    profile={"模式": "烽火地带"})
print(f"  画像有模式+消息有距离 → {r['action']} {r['slots']}")
assert r["action"] == "answer", f"❌ 画像的模式没算进「信息齐全」: {r}"
print("✅ 画像字段视为已提供，不重复反问")

# --- 4. 端到端（Flask test_client，真实流水线）---
print("\n=== 端到端：陈述→落库→跨清空存活→忘记我 ===")
app = create_app()
storage = StorageService(Config.DATA_DIR)
with app.test_client() as client:
    client.get("/")
    with client.session_transaction() as sess:
        uid = sess["user_id"]
    storage.delete_profile(uid)   # 确保从干净状态开始

    resp = client.post("/send_message", json={"message": "我是新手，帮我改把 M4A1"})
    events = [json.loads(l[6:]) for l in resp.get_data(as_text=True).split("\n\n")
              if l.strip().startswith("data: ")]
    meta = next(e for e in events if e.get("type") == "meta")
    assert "新手" in meta["profile"].get("水平", ""), f"❌ meta 没带画像: {meta}"
    assert "新手" in storage.get_profile(uid).get("水平", ""), "❌ 画像没落库"
    print("✅ 陈述后画像落库，meta 事件带回前端")

    client.post("/clear")   # 清空对话：换桌不换牌
    p = client.get("/profile").get_json()["profile"]
    assert "新手" in p.get("水平", ""), f"❌ /clear 把画像弄丢了: {p}"
    print("✅ /clear 后画像存活")

    client.post("/forget_profile")
    assert client.get("/profile").get_json()["profile"] == {}
    assert storage.get_profile(uid) == {}
    print("✅ 忘记我删除生效")

    storage.delete_profile(uid)   # 收尾：不给真实数据库留测试垃圾

print("\n全部通过")

# -*- coding: utf-8 -*-
"""
多意图拆分（①）验证：
1) split_tasks 解析器：好行/坏行/上限/废行（不调 API）
2) 纯多枪代码层组任务（不调 API）
3) 真实链路：8B 判 MULTI → Kimi 拆任务（异构混合）
4) 真实链路：纯多枪不触发 MULTI（省一次主模型调用）
5) 真实链路：多任务缺信息 → 回合级 clarify
6) 存储：search_queries 列写入读取
"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import _multi_gun_tasks
from config import Config
from services import StorageService, AIService
from services.ai_service import tasks_need_clarify, format_task_list

# --- 1. split_tasks 解析器（不调 API）---
print("=== split_tasks 解析器 ===")
p = AIService.split_tasks
r = p("PLAN|枪=M4A1|模式=烽火地带|距离=近战|指定配件=无|取向=无|水平=未知|预算=无\n"
      "QA|问=补偿器和消音器有什么区别")
assert len(r) == 2 and r[0]["type"] == "PLAN" and r[0]["slots"]["枪"] == "M4A1" \
    and r[1]["type"] == "QA" and "补偿器" in r[1]["slots"]["问"], f"正常两行错: {r}"
r = p("我先解释一下\nPLAN|枪=AKM|模式=全面战场|距离=中远\n垃圾行|||\nQA|枪=无")  # QA 缺「问」是废行
assert len(r) == 1 and r[0]["slots"]["枪"] == "AKM", f"坏行没丢干净: {r}"
r = p("PLAN|模式=烽火地带")      # PLAN 没枪是废行
assert r == [], f"无枪 PLAN 没丢: {r}"
r = p("\n".join(f"QA|问=问题{i}" for i in range(5)))   # 超上限截断
assert len(r) == 3, f"上限没截断: {len(r)}"
print("✅ 解析器：好行过 / 坏行丢 / 废行丢 / 上限 3")

# tasks_need_clarify + format_task_list 烟测
assert tasks_need_clarify([{"type": "PLAN", "slots": {"枪": "M4", "模式": "未知", "距离": "近战"}}])
assert not tasks_need_clarify([{"type": "QA", "slots": {"问": "x"}}])
txt = format_task_list([{"type": "PLAN", "slots": {"枪": "M4A1", "模式": "烽火地带", "距离": "近战"}},
                        {"type": "QA", "slots": {"问": "补偿器区别"}}])
assert "1." in txt and "2." in txt and "方案卡" in txt
print("✅ clarify 判定 + 任务清单文本生成")

# --- 2. 纯多枪代码层组任务（不调 API）---
print("\n=== 纯多枪组任务 ===")
r = _multi_gun_tasks({"枪": "M4A1,AKM", "模式": "全面战场", "距离": "近战"})
assert len(r) == 2 and r[0]["slots"]["枪"] == "M4A1" and r[1]["slots"]["枪"] == "AKM" \
    and r[1]["slots"]["模式"] == "全面战场", f"组任务错: {r}"
assert _multi_gun_tasks({"枪": "M4A1"}) == []
print("✅ 逗号枪名 → 每枪一个 PLAN 任务，模式距离共享")

# --- 3. 存储 search_queries 列 ---
s = StorageService(tempfile.mkdtemp())
s.log_request("c1", "多枪测试", search_queries=['{"keywords": "M4A1 改枪"}'])
row = s.get_request_logs(1)[0]
assert "M4A1" in row["search_queries"], f"search_queries 没落库: {row['search_queries']}"
print("✅ search_queries 列写入读取正常")

# --- 4~6. 真实链路（8B + Kimi）---
ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model=Config.LLM_MODEL, use_builtin_search=True,
               router_api_key=Config.ROUTER_API_KEY,
               router_base_url=Config.ROUTER_BASE_URL,
               router_model=Config.ROUTER_MODEL)

print("\n=== 真实链路：异构混合 → MULTI → 拆分 ===")
r = ai.judge_intent([{"role": "user", "content":
                      "M4A1 烽火地带近战怎么改？另外补偿器和消音器有啥区别"}])
print(f"  → action={r['action']} tasks={r.get('tasks')}")
assert r["action"] == "multi", f"❌ 异构混合没判 multi: {r}"
types = [t["type"] for t in r["tasks"]]
assert "PLAN" in types and "QA" in types, f"❌ 拆出的任务类型不对: {r['tasks']}"
plan = next(t for t in r["tasks"] if t["type"] == "PLAN")
assert "M4A1" in plan["slots"].get("枪", "").upper().replace(" ", ""), plan
print("✅ MULTI 判断 + Kimi 拆出 PLAN+QA")

print("\n=== 真实链路：纯多枪不触发 MULTI ===")
r = ai.judge_intent([{"role": "user", "content": "M4A1 和 AKM 都帮我改一下，全面战场打近战"}])
print(f"  → action={r['action']} slots={r['slots']}")
assert r["action"] == "answer", f"❌ 纯多枪误判 multi（多花一次主模型调用）: {r}"
guns = r["slots"].get("枪", "")
assert "M4A1" in guns.upper() and "AKM" in guns.upper(), f"❌ 多枪没提全: {guns}"
assert _multi_gun_tasks(r["slots"]), "❌ 纯多枪组不出任务"
print("✅ 纯多枪走 ANSWER + 代码层组任务（零额外调用）")

print("\n=== 真实链路：多任务缺信息 → 回合级 clarify ===")
r = ai.judge_intent([{"role": "user", "content": "M4A1 怎么改？顺便问下补偿器和消音器区别"}])
print(f"  → action={r['action']} tasks={r.get('tasks')}")
assert r["action"] == "clarify", f"❌ PLAN 缺模式距离没转反问: {r}"
print("✅ 回合级反问（一次问齐，不答任何任务）")

print("\n全部通过")

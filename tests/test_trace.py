# -*- coding: utf-8 -*-
"""
请求日志（⑨ 日志可观测）端到端验证：
走 Flask 测试客户端真实请求 /send_message，确认
1) SSE 里 trace 事件不泄漏给前端
2) request_logs 表里落下字段齐全的一行

用法：python tests/test_trace.py [plan]   加 plan 参数再跑一条完整方案题（慢，约 1~2 分钟）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from config import Config
from services import StorageService


def ask(client, message: str) -> list:
    """发一条消息，返回解析后的 SSE 事件列表"""
    resp = client.post("/send_message",
                       json={"message": message},
                       headers={"Accept": "text/event-stream"})
    events = []
    for line in resp.get_data(as_text=True).split("\n\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def check(message: str, expect_intent: str):
    app = create_app()
    storage = StorageService(Config.DATA_DIR)
    before = storage.get_request_logs(limit=1)
    before_id = before[0]["id"] if before else 0

    with app.test_client() as client:
        client.get("/")   # 拿会话 id
        events = ask(client, message)

    # 1. trace 是内部事件，绝不能出现在发给前端的事件流里
    leaked = [e for e in events if e.get("type") == "trace"]
    assert not leaked, f"trace 泄漏给前端了: {leaked}"
    assert any(e.get("type") == "done" for e in events), "没收到 done"

    # 2. 落库检查：新增一行且关键字段齐全
    after = [r for r in storage.get_request_logs(limit=5) if r["id"] > before_id]
    assert len(after) == 1, f"应新增 1 行日志，实际 {len(after)} 行"
    r = after[0]
    print(f"  落库内容: intent={r['intent']} 搜{r['search_rounds']}轮 "
          f"searched={r['searched']} token={r['prompt_tokens']}+{r['completion_tokens']} "
          f"意图{r['intent_ms']}ms 全程{r['total_ms']}ms 质检={r['validation_issues']}")
    assert r["intent"] == expect_intent, f"意图不符: {r['intent']}"
    assert r["user_message"] == message[:200]
    assert r["total_ms"] > 0 and r["intent_ms"] > 0
    assert r["error"] == "", f"不该有报错: {r['error']}"
    return r


print("💬 闲聊题（快路径）…")
r = check("你好", "chat")
assert r["search_rounds"] == 0 and r["searched"] == 0, "闲聊不该触发搜索"
print("✅ 闲聊路径日志正常（未搜索、字段齐全、trace 未泄漏）")

if "plan" in sys.argv:
    print("\n🔫 方案题（慢路径，含搜索+质检）…")
    r = check("帮我改把 M4A1，全面战场打中远距离", "answer")
    assert r["searched"] == 1, "方案题应触发搜索"
    assert r["search_rounds"] >= 1, f"内置搜索至少 1 轮: {r['search_rounds']}"
    print("✅ 方案路径日志正常（搜索轮数/token/质检均有记录）")

print("\n全部通过")

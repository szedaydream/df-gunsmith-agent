# -*- coding: utf-8 -*-
"""
赞/踩反馈（④ 反馈信息搜集）端到端验证：
走 Flask 测试客户端真实请求，确认
1) done 事件带 message_id
2) 赞/踩落库；重复评价=改票覆盖，不重复建行
3) 坏 rating / 坏 message_id 被拒
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from config import Config
from services import StorageService

app = create_app()
storage = StorageService(Config.DATA_DIR)

with app.test_client() as client:
    client.get("/")
    # 发一条快消息（闲聊路径），拿到 done 事件里的 message_id
    resp = client.post("/send_message", json={"message": "你好"})
    events = [json.loads(l[6:]) for l in resp.get_data(as_text=True).split("\n\n")
              if l.strip().startswith("data: ")]
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[0].get("message_id"), f"done 事件没带 message_id: {done}"
    mid = done[0]["message_id"]
    print(f"✅ done 事件带回 message_id={mid}")

    # 点赞 → 落库
    r = client.post("/feedback", json={"message_id": mid, "rating": 1})
    assert r.status_code == 200 and r.get_json()["success"], r.get_json()
    rows = storage.get_feedbacks()
    assert any(x["message_id"] == mid and x["rating"] == 1 for x in rows), rows
    print("✅ 点赞落库")

    # 改踩 → 覆盖，不重复建行
    r = client.post("/feedback", json={"message_id": mid, "rating": -1})
    assert r.status_code == 200, r.get_json()
    rows = [x for x in storage.get_feedbacks() if x["message_id"] == mid]
    assert len(rows) == 1 and rows[0]["rating"] == -1, f"改票没覆盖: {rows}"
    print("✅ 改票覆盖正常（一条回答只留最新评价）")

    # 坏参数必须被拒
    assert client.post("/feedback", json={"message_id": mid, "rating": 5}).status_code == 400
    assert client.post("/feedback", json={"message_id": mid, "rating": "赞"}).status_code == 400
    assert client.post("/feedback", json={"message_id": 999999, "rating": 1}).status_code == 404
    assert client.post("/feedback", json={}).status_code == 400
    print("✅ 坏 rating / 坏 message_id / 缺参数均被拒")

print("\n全部通过")

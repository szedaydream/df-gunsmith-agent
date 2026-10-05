# -*- coding: utf-8 -*-
"""
请求日志查看器：排障第一站

用法：
    python tests/view_logs.py        # 最近 20 条
    python tests/view_logs.py 50     # 最近 50 条
    python tests/view_logs.py --errors   # 只看有报错/质检未过的
    python tests/view_logs.py --feedback  # 只看赞/踩反馈（带回答摘要）
    python tests/view_logs.py --bad       # 只看踩——优化 prompt 的金矿

每行 = 一次问答：意图、搜了几轮、有没有被打回重搜、质检结果、
token 花了多少、各环节耗时。回答出问题先来这里定位是哪个环节歪的。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from services import StorageService


def fmt_row(r: dict) -> str:
    slots = json.loads(r["slots"]) if r["slots"] else {}
    slot_str = " ".join(f"{k}={v}" for k, v in slots.items()
                        if v and v not in ("无", "未知")) or "-"
    issues = json.loads(r["validation_issues"]) if r["validation_issues"] else []
    flags = []
    if r["forced_research"]:
        flags.append("打回重搜")
    if issues:
        flags.append(f"质检{len(issues)}处")
    if r["fix_retried"]:
        flags.append("已修正")
    if r["error"]:
        flags.append(f"报错:{r['error'][:40]}")
    flag_str = " ⚠️" + ",".join(flags) if flags else ""
    return (f"[{r['id']:>4}] {r['created_at'][5:19]} "
            f"{r['intent']:<8} 搜{r['search_rounds']}轮 "
            f"意图{r['intent_ms']/1000:.1f}s 全程{r['total_ms']/1000:.0f}s "
            f"token {r['prompt_tokens']}+{r['completion_tokens']}"
            f"{flag_str}\n"
            f"       💬 {r['user_message'][:60]}\n"
            f"       📋 {slot_str}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = int(args[0]) if args else 20
    only_errors = "--errors" in sys.argv
    storage = StorageService(Config.DATA_DIR)

    if "--feedback" in sys.argv or "--bad" in sys.argv:
        rating = -1 if "--bad" in sys.argv else None
        rows = storage.get_feedbacks(limit=limit, rating=rating)
        if not rows:
            print("（暂无反馈）")
            return
        for r in rows:
            icon = "👍" if r["rating"] == 1 else "👎"
            snippet = r["content"].replace("\n", " ")[:80]
            print(f"{icon} [{r['created_at'][5:19]}] msg#{r['message_id']} {snippet}…")
        return

    rows = storage.get_request_logs(limit=500 if only_errors else limit)
    if only_errors:
        rows = [r for r in rows
                if r["error"] or r["validation_issues"] not in ("", "[]")
                or r["forced_research"]][:limit]
    if not rows:
        print("（暂无日志）" if not only_errors else "（没有异常记录，一切正常）")
        return
    print(f"最近 {len(rows)} 条请求日志（新的在前）：\n")
    for r in rows:
        print(fmt_row(r))
        print()


if __name__ == "__main__":
    main()

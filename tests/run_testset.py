# -*- coding: utf-8 -*-
"""
测试集自动回归：tests/测试集.md 的 18 题里，16 题可程序化验收

跑法：python tests/run_testset.py
走真实流水线（judge_intent → get_chat_response_stream），和线上唯一区别是不落库。
每题结果分三档：✅ 自动验收通过 / ❌ 自动验收失败 / 👀 标准偏主观，存原文人工核对。
G2（长对话压缩）、G3（清空对话按钮）是 UI 行为，本脚本不覆盖，手动点。

全量约 15 分钟（每个 answer 题都要真联网搜索），回答原文存
tests/测试报告_<日期>.md，人工核对题看那个文件。
"""
import sys, os, json, time, datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from services.ai_service import AIService

ai = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
               model=Config.LLM_MODEL, use_builtin_search=True)
# C1 专用：搜索全关，验证「未经核实」声明
ai_nosearch = AIService(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL,
                        model=Config.LLM_MODEL, use_builtin_search=False)


def run(question, service=None):
    """模拟 app.py 的主流程跑一条消息，返回 (意图, 回答全文, 是否真的搜了)"""
    service = service or ai
    messages = [{"role": "user", "content": question}]
    intent = service.judge_intent(messages)
    answer, searched = [], False
    for ev in service.get_chat_response_stream(
            messages, search_results=None,
            force_clarify=(intent["action"] == "clarify"),
            require_search=(intent["action"] == "answer"),
            slots=intent.get("slots")):
        if ev["type"] == "status" and "资料到手" in ev.get("text", ""):
            searched = True          # 只有模型真调了搜索工具才会有这条状态
        if ev["type"] == "answer":
            answer.append(ev["delta"])
        if ev["type"] == "error":
            answer.append(f"[ERROR] {ev['text']}")
    return intent, "".join(answer), searched


PLAN = "【配件方案】"   # 方案卡标志，clarify/闲聊/问答类都不该出现


def has_plan_card(ans):
    return PLAN in ans and "【属性评估】" in ans


# (题号, 问题, 自动验收函数(intent, answer, searched) -> (True/False/None=人工, 说明))
CASES = [
    ("A1", "帮我改把 M4A1",
     lambda i, a, s: (i["action"] == "clarify" and PLAN not in a, "应反问，不给方案")),
    ("A2", "有什么枪推荐吗",
     lambda i, a, s: (PLAN not in a, "不直接丢方案（语气人工看）")),
    ("A3", "给我整把狙击枪",
     lambda i, a, s: (i["action"] == "clarify" and PLAN not in a, "应反问模式/距离")),
    ("B1", "帮我改把 M4A1，全面战场打中远距离",
     lambda i, a, s: (has_plan_card(a), "完整方案卡")),
    ("B2", "AKM 烽火地带近战怎么配",
     lambda i, a, s: (has_plan_card(a) and "烽火" in a and "近" in a, "定位体现烽火+近战")),
    ("B3", "AWM 全面战场打远点",
     lambda i, a, s: (has_plan_card(a), "方案卡（取向人工看）")),
    ("B4", "预算有限，MP5 烽火地带近战",
     lambda i, a, s: (has_plan_card(a) and ("性价比" in a or "预算" in a or "便宜" in a),
                      "方案卡+性价比取向")),
    ("C2", "M4A1 全面战场中远",
     lambda i, a, s: (has_plan_card(a) and "未经最新资料核实" not in a,
                      "有搜索不该出现「未经核实」声明；补充件标（未核实）人工抽查")),
    ("C3", "有没有「雷神之锤」这个枪口",
     lambda i, a, s: (None, "人工核对：不承认不存在的配件")),
    ("D1", "M4A1 和 AKM 哪个适合新手",
     lambda i, a, s: (PLAN not in a, "对比问答不套方案卡（质量人工看）")),
    ("D2", "枪口补偿器和消音器有什么区别",
     lambda i, a, s: (PLAN not in a and "补偿" in a and "消音" in a, "解释差异")),
    ("E1", "你好",
     lambda i, a, s: (i["action"] == "chat" and not s and PLAN not in a,
                      "闲聊不搜索不丢方案")),
    ("E2", "今天天气怎么样",
     lambda i, a, s: (PLAN not in a, "礼貌引导回主题（语气人工看）")),
    ("F1", "MK47 怎么改？全面战场中距离",
     lambda i, a, s: (has_plan_card(a) and ("1.25" in a or "真实后坐" in a),
                      "围绕 1.25 倍真实后坐力特性展开")),
    ("F2", "腾龙这枪改装要注意什么",
     lambda i, a, s: ("开火稳定性" in a, "提到叠开火稳定性特性")),
    ("F3", "SVCH 我想装 5 倍镜打远点，全面战场",
     lambda i, a, s: ("5倍镜" in a.replace(" ", "") and "【属性评估】" in a,
                      "尊重 5 倍镜选择+属性评估（红线提问人工看）")),
    ("G1", "K416 烽火地带中近距离怎么改",
     lambda i, a, s: (s and "未经最新资料核实" not in a,
                      "真触发了联网搜索；无「未经核实」声明")),
    ("G4", "K437 烽火地带中近距离怎么改",
     lambda i, a, s: ("弹匣座" in a and "（空）" in a or a.count("——") >= 8,
                      "九槽位齐全；来源标注人工抽查")),
]


def main():
    only = {a.upper() for a in sys.argv[1:]}   # 可选：只跑指定题号，如 python run_testset.py G4 F3
    date = datetime.date.today().isoformat()
    # 筛选跑时报告另存，别覆盖全量报告
    suffix = "_" + "_".join(sorted(only)) if only else ""
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               f"测试报告_{date}{suffix}.md")
    lines = [f"# 测试报告 {date}\n",
             f"模型：{Config.LLM_MODEL} / 搜索：{Config.SEARCH_PROVIDER}\n"]
    passed, failed, manual = [], [], []

    # C1 单独跑（搜索全关）
    if not only or "C1" in only:
        print("C1 搜索关闭声明验证…", flush=True)
        t0 = time.time()
        intent, ans, _ = run("M4A1 全面战场中远", service=ai_nosearch)
        ok = "未经" in ans and "核实" in ans
        (passed if ok else failed).append("C1")
        lines.append(f"\n## C1 {'✅' if ok else '❌'} 开头应有「未经最新资料核实」声明\n\n{ans}\n")
        print(f"  {'✅' if ok else '❌'} ({time.time()-t0:.0f}s)", flush=True)

    for cid, q, check in CASES:
        if only and cid not in only:
            continue
        print(f"{cid} {q[:20]}…", flush=True)
        t0 = time.time()
        try:
            intent, ans, searched = run(q)
            ok, note = check(intent, ans, searched)
        except Exception as e:
            intent, ans, searched, ok, note = {}, f"[EXCEPTION] {e}", False, False, "跑挂了"
        tag = "👀" if ok is None else ("✅" if ok else "❌")
        (manual if ok is None else (passed if ok else failed)).append(cid)
        slots = intent.get("slots", {})
        lines.append(f"\n## {cid} {tag} {q}\n\n"
                     f"- 意图：{intent.get('action')} slots={slots} 搜索={searched}\n"
                     f"- 验收：{note}\n\n{ans}\n")
        print(f"  {tag} {note}（{time.time()-t0:.0f}s）", flush=True)

    summary = (f"\n## 汇总\n\n✅ 通过 {len(passed)}：{passed}\n\n"
               f"❌ 失败 {len(failed)}：{failed}\n\n"
               f"👀 人工核对 {len(manual)}：{manual}\n\n"
               f"未覆盖（手动）：G2 长对话压缩、G3 清空对话\n")
    lines.append(summary)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(summary)
    print(f"报告已写入 {report_path}")


if __name__ == "__main__":
    main()

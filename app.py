# -*- coding: utf-8 -*-
"""
Flask 入口：路由层 + 改枪 Agent 的完整流水线

/send_message 的流程（流式版）：
1. 收 JSON、校验非空
2. 取会话 id（session）
3. 存用户消息（落盘）
4. 读最近历史（滑动窗口）
5. 意图判断：clarify / answer / chat
6. 条件搜索：只有 answer 且 need_search 才调搜索 API
7. 以 SSE 流式返回：状态/思考/正文逐段推给前端，正文拼齐后落盘
"""

import json
import logging
import re
import sys
import time
import uuid

from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context

from config import Config
from services import AIService, StorageService, get_search_service
from services.ai_service import PROFILE_FIELDS

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _multi_gun_tasks(slots: dict) -> list:
    """纯多枪（枪字段逗号分隔）在代码层组 PLAN 任务清单——同类型多枪
    不需要 Kimi 拆分（那是异构混合才用的），零额外调用"""
    guns = [g.strip() for g in re.split(r"[，,]", slots.get("枪", ""))
            if g.strip() and g.strip() != "无"]
    if len(guns) < 2:
        return []
    return [{"type": "PLAN", "slots": {**slots, "枪": g}} for g in guns]


def create_app():
    """应用工厂：创建并配置 Flask 应用（第 7 课的结构）"""
    app = Flask(__name__)
    app.config.from_object(Config)
    Config.init_app(app)
    app.secret_key = Config.SECRET_KEY

    # 三个服务实例全局只创建一次，所有请求共用
    ai = AIService(
        api_key=Config.LLM_API_KEY,
        base_url=Config.LLM_BASE_URL,
        model=Config.LLM_MODEL,
        use_builtin_search=(Config.SEARCH_PROVIDER == "kimi_builtin"),
        router_api_key=Config.ROUTER_API_KEY,
        router_base_url=Config.ROUTER_BASE_URL,
        router_model=Config.ROUTER_MODEL,
    )
    search = get_search_service(Config.SEARCH_PROVIDER, Config.SEARCH_API_KEY)
    storage = StorageService(Config.DATA_DIR)

    @app.route("/")
    def index():
        """聊天页面：第一次来访的浏览器发一个专属对话 id + 长期用户 id"""
        if "user_id" not in session:
            session["user_id"] = f"user_{uuid.uuid4().hex[:8]}"
        if "conversation_id" not in session:
            session["conversation_id"] = f"conv_{uuid.uuid4().hex[:8]}"
            logger.info(f"👤 新会话: {session['conversation_id']}")
        return render_template("chat.html")

    @app.route("/send_message", methods=["POST"])
    def send_message():
        # 1. 收：解析前端发来的 JSON，校验非空
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()
        if not user_message:
            return jsonify({"success": False, "error": "消息不能为空"}), 400

        # 2. 桌号 + 熟客牌：conversation_id 是这场对话，user_id 是这个玩家。
        #    /clear 换桌不换牌——画像挂在 user_id 上，跨清空存活
        if "user_id" not in session:
            session["user_id"] = f"user_{uuid.uuid4().hex[:8]}"
        if "conversation_id" not in session:
            session["conversation_id"] = f"conv_{uuid.uuid4().hex[:8]}"
        conv_id = session["conversation_id"]
        user_id = session["user_id"]
        logger.info(f"💬 [{conv_id}] {user_message[:50]}")

        # 3. 存用户消息（落盘）
        storage.add_message(conv_id, "user", user_message)

        # 4. 组上下文。对话短：最近几条原样带上；对话长：旧消息滚动压缩成
        #    「前情提要」——既省 token，又防陈年旧账带偏新话题
        RECENT_KEEP = 6        # 始终原样保留的最近消息数
        COMPRESS_AT = 14       # 总消息数超过这个值就触发压缩
        total = storage.get_message_count(conv_id)
        summary, summarized = storage.get_summary(conv_id)
        if total > COMPRESS_AT:
            # 只压「上次没压过的」那批旧消息（增量压缩，不重复花钱）
            new_old = storage.get_older_messages(
                conv_id, keep_recent=RECENT_KEEP, skip_first=summarized)
            if new_old:
                summary = ai.compress_history(new_old, summary)
                summarized = total - RECENT_KEEP
                storage.set_summary(conv_id, summary, summarized)
        recent = storage.get_recent_messages(conv_id, limit=RECENT_KEEP)
        messages = [{"role": m["role"], "content": m["content"]} for m in recent]
        if summary:
            messages.insert(0, {"role": "system",
                                "content": f"【前情提要】{summary}"})

        def sse(event: dict) -> str:
            """SSE 协议格式：每条事件 = 'data: ' + JSON + 两个换行"""
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        def generate():
            """流式生成器：从意图判断到正文输出全程有状态提示，不让人干等"""
            full_reply = []
            t0 = time.time()          # 全程计时起点（请求级日志用）
            intent_ms = 0             # 意图判断单独计时：router 慢不慢一眼可见
            trace = {}                # ai_service 流结束时抛回的本趟追踪数据
            intent = {}
            searched = False
            profile = storage.get_profile(user_id)   # 跨对话的玩家画像
            try:
                # 5. 意图判断（放进生成器里：先回响应头，前端立刻有状态可看）
                #    画像一并给分类器：画像里已有的字段视为已知，不重复反问
                yield sse({"type": "status", "text": "🧭 正在分析你的需求…"})
                intent = ai.judge_intent(messages, profile=profile)
                intent_ms = int((time.time() - t0) * 1000)

                # 5b. 画像沉淀：这趟提取到的偏好字段（明确陈述才有，见 INTENT_PROMPT
                #     铁律）合并进画像。空值不覆盖，新值盖旧值
                new_prefs = {k: v for k, v in intent.get("slots", {}).items()
                             if k in PROFILE_FIELDS}
                if new_prefs:
                    storage.upsert_profile(user_id, new_prefs)
                    profile = storage.get_profile(user_id)

                # 6. 条件搜索：只有「要回答」且「需要资料」才花搜索额度
                # 搜索词优先用提取出的枪名（比整句用户原话更聚焦，噪声少）
                search_results = None
                if intent.get("action") in ("answer", "multi") \
                        and intent.get("need_search", True):
                    gun = intent.get("slots", {}).get("枪", "")
                    query = (f"三角洲行动 {gun} 改枪" if gun and gun != "无"
                             else f"三角洲行动 {user_message}")
                    search_results = search.search(query) or None
                    if search_results:
                        logger.info(f"🔍 搜索命中 {len(search_results)} 条资料")
                searched = search_results is not None or (
                    intent.get("action") in ("answer", "multi")
                    and Config.SEARCH_PROVIDER == "kimi_builtin")

                # 6b. 任务清单：MULTI 用阶段 2 拆好的；纯多枪（同类型）在代码层
                #     直接组 PLAN 任务，不花 Kimi 拆分的钱
                tasks = intent.get("tasks") or _multi_gun_tasks(
                    intent.get("slots", {}))

                # 发元信息（意图、是否搜索、画像、任务清单），前端可用来调试展示
                yield sse({
                    "type": "meta",
                    "intent": intent.get("action"),
                    "slots": intent.get("slots", {}),
                    "searched": searched,
                    "profile": profile,
                    "tasks": tasks
                })

                # 7. 流式生成（answer 意图强制要求搜索，模型偷懒会被后端打回重搜）
                # trace 事件是内部追踪数据，不转发给前端，攒起来流结束后落库
                for event in ai.get_chat_response_stream(
                        messages, search_results=search_results,
                        force_clarify=(intent.get("action") == "clarify"),
                        require_search=(intent.get("action") in ("answer", "multi")),
                        slots=intent.get("slots"),
                        profile=profile,
                        tasks=tasks or None):
                    if event["type"] == "trace":
                        trace = event.get("data", {})
                        continue
                    if event["type"] == "answer":
                        full_reply.append(event["delta"])
                    yield sse(event)
            except Exception as e:
                logger.error(f"💥 流式生成失败: {e}")
                trace["error"] = trace.get("error") or str(e)
                yield sse({"type": "error", "text": "😔 我遇到了一点技术问题，请稍后再试"})
            # 8. 请求落库：回答正文进 messages，本趟追踪数据进 request_logs。
            #    以后排查「为什么这回答案不好」，先查 request_logs 定位环节
            reply = "".join(full_reply)
            msg_id = None
            if reply:
                msg_id = storage.add_message(conv_id, "assistant", reply)
            storage.log_request(
                conv_id, user_message,
                intent=intent.get("action", ""),
                slots=intent.get("slots"),
                searched=searched,
                search_rounds=trace.get("search_rounds", 0),
                forced_research=trace.get("forced_research", False),
                validation_issues=trace.get("validation_issues", []),
                fix_retried=trace.get("fix_retried", False),
                prompt_tokens=trace.get("prompt_tokens", 0),
                completion_tokens=trace.get("completion_tokens", 0),
                intent_ms=intent_ms,
                total_ms=int((time.time() - t0) * 1000),
                error=trace.get("error", ""),
                search_queries=trace.get("search_queries", []),
            )
            # message_id 给前端：赞/踩按钮靠它指认评价的是哪条回答
            yield sse({"type": "done", "message_id": msg_id})

        # SSE 响应：流式推送，禁用缓存和反向代理缓冲
        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.route("/feedback", methods=["POST"])
    def feedback():
        """赞/踩：对某条 AI 回答的评价入库。同一条回答重复评价=改主意，覆盖"""
        data = request.get_json(silent=True) or {}
        try:
            message_id = int(data.get("message_id"))
            rating = int(data.get("rating"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "参数格式不对"}), 400
        if rating not in (1, -1):
            return jsonify({"success": False, "error": "rating 只能是 1 或 -1"}), 400
        if not storage.add_feedback(message_id, rating):
            return jsonify({"success": False, "error": "这条回答不存在"}), 404
        logger.info(f"{'👍' if rating == 1 else '👎'} 收到反馈: message_id={message_id}")
        return jsonify({"success": True})

    @app.route("/profile", methods=["GET"])
    def get_profile():
        """当前用户的画像（前端「已记住」标签的数据源）"""
        user_id = session.get("user_id")
        return jsonify({"profile": storage.get_profile(user_id) if user_id else {}})

    @app.route("/forget_profile", methods=["POST"])
    def forget_profile():
        """「忘记我」：删掉画像（对话记录不动）。下次提取到新偏好会重新积累"""
        user_id = session.get("user_id")
        if user_id:
            storage.delete_profile(user_id)
            logger.info(f"🧠 画像已清除: {user_id}")
        return jsonify({"success": True})

    @app.route("/clear", methods=["POST"])
    def clear():
        """清空当前对话：删库里的记录 + 给这个浏览器换发新会话 id"""
        old_id = session.get("conversation_id")
        if old_id:
            storage.delete_conversation(old_id)
            logger.info(f"🗑️ 清空对话: {old_id}")
        session["conversation_id"] = f"conv_{uuid.uuid4().hex[:8]}"
        return jsonify({"success": True})

    @app.route("/health")
    def health():
        """健康检查：监控 AI 服务和存储是否正常"""
        return jsonify({
            "status": "healthy" if ai.test_connection() else "degraded",
            "search_provider": Config.SEARCH_PROVIDER
        })

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)

# -*- coding: utf-8 -*-
"""
存储服务：对话记录持久化到 SQLite（第 8 课升级版，替代原 JSON 文件方案）

为什么从 JSON 换成 SQLite：
- JSON 方案每次写消息都要「读全量 → 改 → 整体写回」，对话越多越慢，
  两个请求同时写还会互相覆盖（丢消息）
- SQLite 是 Python 自带的正经数据库（零安装、一个文件就是一个库）：
  写入是追加一行，不用整文件读写；数据库自己会处理并发写的排队
- 「取最近 N 条」从 Python 切片变成 SQL 的 ORDER BY + LIMIT，语义更直接

表结构：
- conversations：一场对话一行（id / 创建时间 / 最后活跃时间）
- messages：一条消息一行（所属对话 id / 角色 / 内容 / 时间戳）
- request_logs：一次请求一行（可观测性：意图/搜索/质检/耗时/报错），
  出问题不用翻控制台，直接查库定位是哪一步歪了

对外接口和 JSON 版完全一致（add_message / get_recent_messages），
所以 app.py 一行都不用改——这就是「面向接口编程」的红利。
"""

import json
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)


class StorageService:
    """存储服务类：对话记录的读写与检索（SQLite 版）"""

    def __init__(self, data_dir: str):
        self.db_path = os.path.join(data_dir, "conversations.db")
        os.makedirs(data_dir, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """每次操作开一个短连接。SQLite 轻量，这样写最省心且线程安全"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row   # 查询结果可以按列名取值
        return conn

    def _init_db(self) -> None:
        """建表（IF NOT EXISTS：重复启动不会清空旧数据）"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )""")
            # 历史压缩功能后加的两列：已压缩出的摘要 + 压缩覆盖到第几条。
            # 老库没有这两列，ALTER 补上；已存在会报 OperationalError，忽略即可
            for ddl in (
                "ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE conversations ADD COLUMN summarized_count INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            # 请求级日志：每次问答一行。排障时先查这里——意图判错、搜索没触发、
            # 质检打回、耗时异常，一眼定位到环节，不用在控制台输出里大海捞针
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT '',
                    slots TEXT NOT NULL DEFAULT '',
                    searched INTEGER NOT NULL DEFAULT 0,
                    search_rounds INTEGER NOT NULL DEFAULT 0,
                    forced_research INTEGER NOT NULL DEFAULT 0,
                    validation_issues TEXT NOT NULL DEFAULT '',
                    fix_retried INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    intent_ms INTEGER NOT NULL DEFAULT 0,
                    total_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )""")
            # 后加的列：模型实际搜了什么 query（多任务时查搜索覆盖用）。
            # 必须放在 CREATE 之后——新库先建表才有得改，老库靠 ALTER 补列
            try:
                conn.execute("ALTER TABLE request_logs ADD COLUMN"
                             " search_queries TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 用户反馈：对某条 AI 回答的赞/踩。一条回答只存一个最新评价
            # （UNIQUE(message_id)，改主意就是覆盖）。踩的样本是以后
            # 优化 prompt 和补测试集的金矿
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedbacks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )""")
            # 用户画像：跨对话记住玩家的稳定偏好（水平/取向/预算/模式）。
            # 挂 user_id 不挂 conversation_id——清空对话清的是聊天记录，
            # 不是玩家的人设。画像存 JSON 一个字段：字段集和提取器的
            # slots 保持一致（中文键），加了新偏好字段不用改表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles(
                    user_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )""")

    # ---------- 对外接口 ----------
    def add_message(self, conversation_id: str, role: str, content: str) -> int:
        """往指定对话追加一条消息；对话不存在则新建（INSERT OR IGNORE）。
        返回新消息 id（赞/踩按钮要靠它指认是哪条回答）"""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations(id, created_at, updated_at)"
                " VALUES(?, ?, ?)",
                (conversation_id, now, now)
            )
            cur = conn.execute(
                "INSERT INTO messages(conversation_id, role, content, timestamp)"
                " VALUES(?, ?, ?, ?)",
                (conversation_id, role, content, now)
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id)
            )
        return cur.lastrowid

    def get_recent_messages(self, conversation_id: str, limit: int = 10) -> list:
        """取指定对话的最近 N 条（按自增 id 倒序取 N 条，再翻回正序）"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp FROM messages"
                " WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------- 历史压缩配套 ----------
    def get_message_count(self, conversation_id: str) -> int:
        """这场对话一共有多少条消息（判断要不要压缩用）"""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (conversation_id,)
            ).fetchone()[0]

    def get_older_messages(self, conversation_id: str, keep_recent: int,
                           skip_first: int = 0) -> list:
        """
        取「旧消息」：正序排列里，跳过前 skip_first 条（已压缩过的），
        再去掉最后 keep_recent 条（要原样保留的），中间的就是待压缩的。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ?"
                " ORDER BY id ASC",
                (conversation_id,)
            ).fetchall()
        middle = rows[skip_first: len(rows) - keep_recent if keep_recent else None]
        return [dict(r) for r in middle]

    def get_summary(self, conversation_id: str) -> tuple:
        """取当前摘要和已压缩覆盖的消息数：(summary, summarized_count)"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary, summarized_count FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()
        return (row["summary"], row["summarized_count"]) if row else ("", 0)

    def set_summary(self, conversation_id: str, summary: str,
                    summarized_count: int) -> None:
        """写回新摘要和覆盖进度"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET summary = ?, summarized_count = ?"
                " WHERE id = ?",
                (summary, summarized_count, conversation_id)
            )

    def delete_conversation(self, conversation_id: str) -> None:
        """清空一场对话：消息和对话记录一起删"""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?",
                         (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?",
                         (conversation_id,))

    # ---------- 请求级日志（可观测性） ----------
    def log_request(self, conversation_id: str, user_message: str,
                    intent: str = "", slots: dict = None,
                    searched: bool = False, search_rounds: int = 0,
                    forced_research: bool = False,
                    validation_issues: list = None, fix_retried: bool = False,
                    prompt_tokens: int = 0, completion_tokens: int = 0,
                    intent_ms: int = 0, total_ms: int = 0,
                    error: str = "", search_queries: list = None) -> None:
        """
        一次问答落一行日志。这里失败绝不能影响主流程——日志写不进去
        最多丢一条排障线索，不能让用户的回答跟着挂掉，所以整个吞异常。
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO request_logs("
                    "conversation_id, user_message, intent, slots, searched,"
                    "search_rounds, forced_research, validation_issues,"
                    "fix_retried, prompt_tokens, completion_tokens,"
                    "intent_ms, total_ms, error, search_queries, created_at)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (conversation_id, user_message[:200], intent,
                     json.dumps(slots or {}, ensure_ascii=False),
                     int(searched), search_rounds, int(forced_research),
                     json.dumps(validation_issues or [], ensure_ascii=False),
                     int(fix_retried), prompt_tokens, completion_tokens,
                     intent_ms, total_ms, error[:500],
                     json.dumps(search_queries or [], ensure_ascii=False),
                     datetime.now().isoformat())
                )
        except Exception as e:
            logger.warning(f"请求日志写入失败（不影响主流程）: {e}")

    def get_request_logs(self, limit: int = 20) -> list:
        """取最近的请求日志（新的在前），给查看器/排障用"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM request_logs ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 用户反馈（赞/踩） ----------
    def add_feedback(self, message_id: int, rating: int) -> bool:
        """
        给某条 AI 回答记一个评价（1=赞 / -1=踩）。同一条回答重复评价
        视为改主意，覆盖旧值。消息不存在返回 False（前端传了坏 id）。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT conversation_id FROM messages WHERE id = ?",
                (message_id,)
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "INSERT INTO feedbacks(message_id, conversation_id, rating, created_at)"
                " VALUES(?, ?, ?, ?)"
                " ON CONFLICT(message_id) DO UPDATE SET"
                " rating = excluded.rating, created_at = excluded.created_at",
                (message_id, row["conversation_id"], rating,
                 datetime.now().isoformat())
            )
        return True

    def get_feedbacks(self, limit: int = 50, rating: int = None) -> list:
        """取反馈列表（带上被评价的回答内容摘要，新的在前）。
        rating=1 只看赞、-1 只看踩、None 全看"""
        sql = ("SELECT f.id, f.message_id, f.rating, f.created_at,"
               " m.content FROM feedbacks f"
               " JOIN messages m ON m.id = f.message_id")
        args = []
        if rating is not None:
            sql += " WHERE f.rating = ?"
            args.append(rating)
        sql += " ORDER BY f.id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---------- 用户画像（跨对话的稳定偏好） ----------
    def get_profile(self, user_id: str) -> dict:
        """取用户画像（没有返回空 dict）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile FROM user_profiles WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        return json.loads(row["profile"]) if row else {}

    def upsert_profile(self, user_id: str, fields: dict) -> None:
        """
        把新提取到的偏好字段合并进画像。铁律：空值/无/未知绝不落库——
        用户这次没提取向，不能把已记住的「性价比」擦掉。
        和请求日志一样：画像写失败不能炸主流程。
        """
        clean = {k: v for k, v in fields.items()
                 if v and v not in ("无", "未知")}
        if not clean:
            return
        try:
            profile = self.get_profile(user_id)
            profile.update(clean)
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO user_profiles(user_id, profile, updated_at)"
                    " VALUES(?, ?, ?)"
                    " ON CONFLICT(user_id) DO UPDATE SET"
                    " profile = excluded.profile, updated_at = excluded.updated_at",
                    (user_id, json.dumps(profile, ensure_ascii=False),
                     datetime.now().isoformat())
                )
        except Exception as e:
            logger.warning(f"画像写入失败（不影响主流程）: {e}")

    def delete_profile(self, user_id: str) -> None:
        """「忘记我」：删掉这个用户的画像"""
        with self._connect() as conn:
            conn.execute("DELETE FROM user_profiles WHERE user_id = ?",
                         (user_id,))

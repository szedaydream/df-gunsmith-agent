# -*- coding: utf-8 -*-
"""
配置中心：所有"会变的东西"都集中在这里。

设计要点（对应第 7 课的知识）：
1. 密钥、地址、模型名全部从 .env 读，代码里零明文秘密
2. 大模型和搜索服务都做成「可插拔」——开源后用户只改 .env 就能换成
   自己的 DeepSeek/Kimi 和自己的搜索服务，业务代码一行不用动
3. os.environ.get("名字", 默认值)：环境变量没配时用默认值保底，程序不崩
"""

import os
from dotenv import load_dotenv

# 把 .env 文件的内容倒进环境变量（.env → 环境变量，方向别记反）
load_dotenv()


class Config:
    # ---------- Flask 基础配置 ----------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    DEBUG = os.environ.get("DEBUG", "True").lower() == "true"
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))

    # ---------- 大模型配置（OpenAI 兼容厂商通吃）----------
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen/Qwen3-8B")

    # ---------- Router 小模型（意图判断+信息提取专用）----------
    # 架构思路：分类/提取这种粗活用便宜的免费小模型，生成才动用贵模型。
    # 不配 ROUTER_* 时自动回退用主模型，单 key 用户无感。
    ROUTER_API_KEY = os.environ.get("ROUTER_API_KEY", "") or LLM_API_KEY
    ROUTER_BASE_URL = os.environ.get("ROUTER_BASE_URL", "") or LLM_BASE_URL
    ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "") or LLM_MODEL

    # ---------- 搜索服务配置 ----------
    # 可选值：
    #   none          不联网搜索（默认，所有推荐标注为模型经验）
    #   bocha         博查搜索（国内，专为 AI 设计，open.bochaai.com 申请）
    #   tavily        Tavily 搜索（海外流行）
    #   kimi_builtin  仅当 LLM 是 Kimi 时可用：搜索是模型的内置工具
    SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "none")
    SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY", "")

    # ---------- 数据存储 ----------
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    @staticmethod
    def init_app(app):
        """启动时检查关键配置，早暴露问题"""
        os.makedirs(Config.DATA_DIR, exist_ok=True)
        if not Config.LLM_API_KEY:
            print("⚠️  警告：.env 里没有配置 LLM_API_KEY")
        if Config.SEARCH_PROVIDER == "none":
            print("ℹ️  搜索未启用（SEARCH_PROVIDER=none），方案将基于模型经验并标注")

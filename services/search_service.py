# -*- coding: utf-8 -*-
"""
搜索服务：抽象层 + 可插拔适配器

为什么这样设计：
- 所有搜索适配器都长一个样子：search(query, count) -> [ {"title", "snippet", "url"}, ... ]
- 上层（app.py）只认这个统一接口，不关心底下是博查还是 Tavily
- 想换搜索服务？改 .env 里的 SEARCH_PROVIDER 即可，业务代码零改动
- 这就是「面向接口编程」，面试可以讲：抽象层隔离了第三方 API 的差异
"""

import logging
import requests

logger = logging.getLogger(__name__)


class NullSearch:
    """
    空搜索：未配置搜索服务时的降级实现。

    永远返回空列表。好处是上层代码不用写 if/else 判断"有没有搜索服务"——
    调了等于没调，系统自然降级为「纯模型经验 + 全部标注未核实」。
    这种写法叫「空对象模式」（Null Object Pattern）。
    """

    def search(self, query: str, count: int = 5) -> list:
        logger.info("🔍 搜索未启用，跳过联网搜索")
        return []


class BochaSearch:
    """
    博查搜索适配器（国内，专为 AI 场景设计）
    申请地址：https://open.bochaai.com
    特点：返回带 AI 摘要（summary），不用自己解析 HTML
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://api.bochaai.com/v1/web-search"

    def search(self, query: str, count: int = 5) -> list:
        try:
            resp = requests.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={"query": query, "count": count, "summary": True},
                timeout=15
            )
            if resp.status_code != 200:
                logger.error(f"博查搜索失败: HTTP {resp.status_code}")
                return []   # 搜索失败不抛异常，降级为无资料回答

            # 博查返回结构：data.webPages.value 是结果列表
            items = (resp.json().get("data", {})
                                .get("webPages", {})
                                .get("value", []))
            return [
                {
                    "title": it.get("name", ""),
                    "snippet": it.get("summary") or it.get("snippet", ""),
                    "url": it.get("url", "")
                }
                for it in items
            ]
        except Exception as e:
            logger.error(f"博查搜索异常: {e}")
            return []


class TavilySearch:
    """
    Tavily 搜索适配器（海外最流行的 AI 搜索 API）
    申请地址：https://tavily.com（每月 1000 次免费）
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, count: int = 5) -> list:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": count
                },
                timeout=15
            )
            if resp.status_code != 200:
                logger.error(f"Tavily 搜索失败: HTTP {resp.status_code}")
                return []

            return [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("content", ""),
                    "url": r.get("url", "")
                }
                for r in resp.json().get("results", [])
            ]
        except Exception as e:
            logger.error(f"Tavily 搜索异常: {e}")
            return []


def get_search_service(provider: str, api_key: str):
    """
    工厂函数：按配置创建对应的搜索适配器。

    上层只需要调这一个函数，不用关心 if/else 的创建逻辑。
    provider 无法识别或缺 key 时，自动降级为 NullSearch。
    """
    if provider == "bocha" and api_key:
        logger.info("🔍 搜索服务：博查")
        return BochaSearch(api_key)
    if provider == "tavily" and api_key:
        logger.info("🔍 搜索服务：Tavily")
        return TavilySearch(api_key)
    if provider == "kimi_builtin":
        # Kimi 的搜索是模型内置工具，不走外部 API，由 AIService 处理
        logger.info("🔍 搜索服务：Kimi 模型内置搜索")
        return NullSearch()
    if provider != "none":
        logger.warning(f"⚠️ 未知的搜索配置 {provider}（或缺 key），已降级为不搜索")
    return NullSearch()

# -*- coding: utf-8 -*-
"""
枪械档案库：人工核对过的「地面真相」

为什么有这个文件（取代原 weapon_traits.py）：
- 模型没玩过游戏，搜索到的攻略本身有错——槽位数、配件名这种硬事实
  不能靠模型凭印象，要有一份人核对过的数据让它查
- 数据在 data/gun_profiles.json，玩家（或开源后的贡献者）直接改 JSON
  就能纠错/补枪，不用碰代码；带热更新，改完下次提问即生效
- prompt 里规定：档案与搜索冲突时，以档案为准
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

PROFILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "gun_profiles.json")

_cache = {"mtime": None, "profiles": {}}

# 强度档位排序：精选方案时按强度从高到低取
TIER_RANK = {"S+": 0, "S": 1, "S-": 2, "A+": 3, "A": 4, "B": 5, "C": 6}
BUILD_LIMIT = 4   # 每把枪最多注入 prompt 的方案数，省 token；其余留在档案里随要随取


def _select_builds(builds: list) -> tuple:
    """
    从 builds 里精选注入 prompt 的子集。返回 (选中列表, 被省略的方案数)。
    策略：按强度 tier 从高到低取前 BUILD_LIMIT 条；
    如果选中集里没有「性价比」方案而档案里有，用最后一名换掉——
    预算有限的玩家问性价比时，prompt 里不能一条便宜方案都没有。
    """
    dicts = [b for b in builds if isinstance(b, dict)]
    others = [b for b in builds if not isinstance(b, dict)]
    ranked = sorted(dicts, key=lambda b: TIER_RANK.get(b.get("tier", ""), 9))
    picked = ranked[:BUILD_LIMIT]
    if picked and not any("性价比" in b.get("name", "") for b in picked):
        budget = next((b for b in ranked[BUILD_LIMIT:] if "性价比" in b.get("name", "")), None)
        if budget:
            picked[-1] = budget
    return others + picked, len(ranked) - len(picked)


def _load() -> dict:
    """读 JSON 并缓存；文件修改时间变了才重读（热更新，改完不用重启服务）"""
    try:
        mtime = os.path.getmtime(PROFILE_PATH)
    except OSError:
        return {}
    if mtime != _cache["mtime"]:
        try:
            with open(PROFILE_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            # 下划线开头的是说明性字段，不是枪
            _cache["profiles"] = {k: v for k, v in raw.items() if not k.startswith("_")}
            _cache["mtime"] = mtime
            logger.info(f"📚 枪械档案已加载: {list(_cache['profiles'].keys())}")
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"枪械档案读取失败，沿用旧缓存: {e}")
    return _cache["profiles"]


def find_profiles(text: str) -> dict:
    """
    在文本里找已收录的枪械档案。
    返回 {武器名: 档案 dict}。
    匹配方式：含中文的名字用子串匹配；纯英文数字的名字（mk4、M4）必须带词边界，
    否则 "mk47" 会误命中 "mk4"。
    """
    profiles = _load()
    lowered = text.lower()
    hits = {}

    def matched(name: str) -> bool:
        n = name.lower()
        if re.search(r"[一-鿿]", n):          # 含中文：子串即可
            return n in lowered
        return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", lowered) is not None

    for name, profile in profiles.items():
        names = [name] + profile.get("aliases", [])
        if any(matched(n) for n in names):
            hits[name] = profile
    return hits


def format_for_prompt(profiles: dict) -> str:
    """把命中的档案格式化成 prompt 用的文本块"""
    blocks = []
    for name, p in profiles.items():
        lines = [f"◆ {name}（{p.get('category', '未知分类')}）"
                 + ("" if p.get("verified") else "（本档案部分数据待核对）")]
        if p.get("ammo"):
            lines.append(f"弹药：{p['ammo']}")
        if p.get("slots"):
            slots = p["slots"]
            lines.append(f"可改装槽位（共 {len(slots)} 个）：{'、'.join(slots)}")
        else:
            lines.append("可改装槽位：档案未收录（按规则 3 交叉验证，查不到就明说）")
        for note in p.get("slot_notes", []):
            lines.append(f"槽位备注：{note}")
        if p.get("traits"):
            lines.append(f"武器特性：{p['traits']}")
        if p.get("notes"):
            lines.append(f"实战印象：{p['notes']}")
        picked, hidden = _select_builds(p.get("builds", []))
        for b in picked:
            if isinstance(b, dict):
                tier = f"（强度 {b['tier']}）" if b.get("tier") else ""
                lines.append(f"成熟方案：{b.get('name', '')}{tier} 改枪码 {b.get('code', '')}")
            else:
                lines.append(f"成熟方案：{b}")
        if hidden > 0:
            lines.append(f"（档案里还有 {hidden} 套方案未列出，玩家想要更多方案时再说）")
        cc = p.get("congcong")
        if cc:
            lines.append(f"聪聪评级：{cc.get('tier', '?')}（{cc.get('season', '')}赛季）"
                         + (f" 点评：{cc['review']}" if cc.get("review") else ""))
        if p.get("source"):
            lines.append(f"档案来源：{p['source']}（更新于 {p.get('updated', '未知')}）")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

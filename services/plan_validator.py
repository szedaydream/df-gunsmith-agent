# -*- coding: utf-8 -*-
"""
方案出厂质检器：零 token 的规则校验

为什么有这层：模型写的方案卡可能结构缺段、槽位数和档案对不上、
配件忘了标来源——这些错误规则就能查，不值得也不应该再花一次 LLM 调用。
质检发生在流式输出结束后，发现问题把问题清单喂回模型自动修正一次。

多卡支持（① 多意图拆分）：一次回答可能含多张方案卡（多枪场景），
按【枪械】段切开后每张卡独立过检，问题带枪名前缀合并返回。
单卡回答的行为和旧版完全一致。

检查项（每张卡独立检查，全部通过才放行）：
1. 结构完整：方案卡六个【段落】齐全
2. 来源标注：【配件方案】里每条配件行必须带（来源）标注
3. 槽位自洽：声称「共 N 个配件槽」就要列出 N 条
4. 槽位对档案：该卡的枪命中档案且有槽位数据时，N 必须等于档案槽位数
   （配件改变槽位数是合法情形——修正指令里允许模型说明依据）
5. 红线覆盖：档案填了 red_lines 的枪，【属性评估】必须逐项提到
   （red_lines 目前全部为空，核查作业填上即自动激活）
"""
import logging
import re

logger = logging.getLogger(__name__)

REQUIRED_SECTIONS = ["枪械", "定位", "配件方案", "精校建议", "属性评估", "使用建议"]


def _section(answer: str, name: str) -> str:
    """取出【name】到下一个【标题】之间的内容"""
    m = re.search(rf"【{name}】(.*?)(?=【|$)", answer, re.S)
    return m.group(1) if m else ""


def _split_cards(answer: str) -> list:
    """
    按【枪械】头把回答切成方案卡。返回 [(枪名, 卡文本), ...]。
    没有【枪械】头但有【配件方案】的，整篇当一张匿名卡（旧格式兼容）。
    """
    marks = list(re.finditer(r"【枪械】([^\n（(（】]*)", answer))
    if not marks:
        return [("", answer)] if "【配件方案】" in answer else []
    cards = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(answer)
        cards.append((m.group(1).strip(), answer[m.start():end]))
    return cards


def _match_profile(gun: str, profiles: dict) -> dict:
    """卡上的枪名对档案 key（双向包含、忽略大小写，和意图交叉验证同款逻辑）"""
    if not gun:
        return None
    for name, p in profiles.items():
        if gun.lower() in name.lower() or name.lower() in gun.lower():
            return p
    return None


def _validate_card(card: str, gun: str, profile: dict) -> list:
    """单张方案卡的五项检查"""
    issues = []
    # 1. 结构完整
    for sec in REQUIRED_SECTIONS:
        if f"【{sec}】" not in card:
            issues.append(f"缺【{sec}】段")

    plan_sec = _section(card, "配件方案")
    item_lines = [l for l in plan_sec.splitlines()
                  if l.strip().startswith(("-", "·", "*"))]

    # 2. 每条配件带来源标注
    for l in item_lines:
        if "（" not in l or "）" not in l:
            issues.append(f"配件缺来源标注：{l.strip()[:25]}")

    # 3 & 4. 槽位数量：自洽 + 对档案
    m = re.search(r"共\s*(\d+)\s*个配件槽", card)
    if m and item_lines:
        claimed = int(m.group(1))
        if claimed != len(item_lines):
            issues.append(f"声称共 {claimed} 个槽但只列了 {len(item_lines)} 条")
        if profile and profile.get("slots"):
            archive_n = len(profile["slots"])
            if claimed != archive_n:
                issues.append(
                    f"槽位数 {claimed} 与档案不符（档案为 {archive_n} 个："
                    f"{'、'.join(profile['slots'])}）。若因配件改变了槽位数，"
                    f"必须在方案里写明哪个配件增减了槽位")

    # 5. 红线覆盖（档案 red_lines 为空则跳过）
    eval_sec = _section(card, "属性评估")
    if profile:
        for stat, line in profile.get("red_lines", {}).items():
            if stat not in eval_sec:
                issues.append(f"【属性评估】未检查红线属性「{stat}」（红线：{line}）")
    return issues


def validate_plan(answer: str, profiles: dict) -> list:
    """
    质检回答里的所有方案卡。profiles 是本轮命中的枪械档案（find_profiles 的结果）。
    非方案类回答（没有【配件方案】）直接放行——问答/闲聊不归这里管。
    """
    issues = []
    if "【配件方案】" not in answer:
        return issues
    cards = _split_cards(answer)
    multi = len(cards) > 1
    for gun, card in cards:
        for issue in _validate_card(card, gun, _match_profile(gun, profiles)):
            issues.append(f"[{gun}] {issue}" if multi and gun else issue)
    return issues


def build_fix_instruction(issues: list) -> str:
    """把问题清单变成给模型的修正指令（打回重写时塞给它）"""
    items = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(issues))
    return ("你刚才的方案未通过出厂质检，问题如下：\n" + items +
            "\n请修正后重新输出完整方案卡，只输出修正后的方案，不要解释。")

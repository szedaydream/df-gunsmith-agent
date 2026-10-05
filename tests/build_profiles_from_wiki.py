# -*- coding: utf-8 -*-
"""
一次性生成脚本：萌娘百科武器词条 → data/gun_profiles.json

数据源：data/wiki_text.txt（Playwright 从萌娘百科「三角洲行动/武器与配件」抓的全文）
做法：按 h4 枪名切片 → 逐枪抽「使用弹药 / 可改装部分 / 独特改装部分 / 游戏内表现」
      → 繁转简 → 手工补充别名表和已核实特性（聪聪）→ 写 JSON。
全部标 verified: false（等玩家核查），已有的人工核实条目（MK47/腾龙特性）保留。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
from opencc import OpenCC

t2s_raw = OpenCC("t2s").convert


def t2s(s: str) -> str:
    """繁转简 + opencc 漏网的常见异体字"""
    return (t2s_raw(s)
            .replace("鎗", "枪").replace("著", "着").replace("裡", "里")
            .replace("隻", "只").replace("彆", "别扭").replace("砲", "炮"))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (wiki h4 标题（繁）, 档案键名（简）, [别名], 分类)
ROSTER = [
    ("UZI衝鋒鎗", "UZI", [], "冲锋枪"),
    ("野牛衝鋒鎗", "野牛", ["PP-19", "野牛冲锋枪"], "冲锋枪"),
    ("勇士衝鋒鎗", "勇士", ["PP-19-01", "勇士冲锋枪"], "冲锋枪"),
    ("MP5衝鋒鎗", "MP5", ["mp5"], "冲锋枪"),
    ("SMG-45衝鋒鎗", "SMG-45", ["smg45"], "冲锋枪"),
    ("SR-3M緊湊突擊步槍", "SR-3M", ["SR3M", "sr-3m"], "冲锋枪"),
    ("P90衝鋒鎗", "P90", ["p90"], "冲锋枪"),
    ("MP7衝鋒鎗", "MP7", ["mp7"], "冲锋枪"),
    ("Vector衝鋒鎗", "Vector", ["维克托", "vector", "短剑"], "冲锋枪"),
    ("QCQ171衝鋒鎗（二〇式衝鋒鎗）", "QCQ171", ["QCQ-171", "qcq171"], "冲锋枪"),
    ("MK4衝鋒鎗", "MK4", ["mk4"], "冲锋枪"),
    ("G17手槍", "G17", [], "手枪"),
    ("QSZ92G手槍（九二式改進型半自動手槍）", "QSZ92G", ["92G", "九二式"], "手枪"),
    ("沙漠之鷹", "沙漠之鹰", ["沙鹰"], "手枪"),
    ("93R手槍", "93R", [], "手枪"),
    ("G18手槍", "G18", [], "手枪"),
    (".357左輪手槍", ".357左轮", ["左轮", "357"], "手枪"),
    ("M1911", "M1911", [], "手枪"),
    ("CAR-15突擊步槍（豌豆射手）", "CAR-15", ["car15"], "突击步枪"),
    ("AKS-74U突擊步槍", "AKS-74U", ["74U", "aks74u"], "突击步枪"),
    ("M4A1突擊步槍", "M4A1", ["m4a1", "M4"], "突击步枪"),
    ("AKM突擊步槍", "AKM", ["akm"], "突击步枪"),
    ("QBZ-95-1突擊步槍（九五式自動步槍）", "QBZ-95-1", ["95-1", "95式", "QBZ95"], "突击步枪"),
    ("K416突擊步槍", "K416", ["k416", "HK416"], "突击步枪"),
    ("K437突擊步槍", "K437", ["k437", "HK437"], "突击步枪"),
    ("AK-12突擊步槍", "AK-12", ["AK12", "ak12"], "突击步枪"),
    ("PTR-32突擊步槍", "PTR-32", ["ptr32"], "突击步枪"),
    ("AS Val「巨浪」突擊步槍（劉濤融化器）", "AS Val", ["巨浪", "as val", "ASVAL"], "突击步枪"),
    ("騰龍突擊步槍（QBZ-191突擊步槍）（二〇式自動步槍）", "腾龙", ["QBZ-191", "CI-19", "腾龙突击步枪", "191"], "突击步枪"),
    ("AUG突擊步槍", "AUG", ["aug"], "突击步枪"),
    ("SG552突擊步槍（糖豆發射器）", "SG552", ["sg552"], "突击步枪"),
    ("M16A4突擊步槍（M16連狙）", "M16A4", ["M16", "m16a4"], "突击步枪"),
    ("G3戰鬥步槍", "G3", ["g3"], "突击步枪"),
    ("M7戰鬥步槍", "M7", ["m7"], "突击步枪"),
    ("SCAR-H戰鬥步槍", "SCAR-H", ["scar-h", "SCAR", "死嘎"], "突击步枪"),
    ("ASh-12戰鬥步槍（經典款玻璃大砲）", "ASh-12", ["ash-12", "ASH12", " ash12"], "突击步枪"),
    ("KC17突擊步槍", "KC17", ["kc17", "AM-17"], "突击步枪"),
    ("MK47突擊步槍", "MK47", ["mk47"], "突击步枪"),
    ("MCX LT突擊步槍", "MCX LT", ["MCX", "mcx"], "突击步枪"),
    ("AR-57突擊步槍", "AR-57", ["AR57", "ar57"], "突击步枪"),
    ("RM277突擊步槍", "RM277", ["rm277"], "突击步枪"),
    ("SR9射手步槍", "SR9", ["sr9"], "精确射手步枪"),
    ("Mini-14射手步槍", "Mini-14", ["mini14", "迷你14"], "精确射手步枪"),
    ("M14戰鬥射手步槍", "M14", ["m14", "妹控"], "精确射手步枪"),
    ("SKS射手步槍", "SKS", ["sks"], "精确射手步枪"),
    ("PSG-1射手步槍", "PSG-1", ["psg1"], "精确射手步枪"),
    ("VSS射手步槍", "VSS", ["vss"], "精确射手步枪"),
    ("SVD狙擊步槍", "SVD", ["svd"], "精确射手步枪"),
    ("SR-25射手步槍", "SR-25", ["sr25"], "精确射手步枪"),
    ("槓桿式步槍Marlin槓桿步槍", "Marlin", ["杠杆步枪", "杠杆式步枪", "marlin"], "精确射手步枪"),
    ("SVCH射手步槍", "SVCH", ["svch"], "精确射手步枪"),
    ("M249輕機槍", "M249", ["m249", "大菠萝"], "轻机枪"),
    ("M250通用機槍", "M250", ["m250"], "轻机枪"),
    ("PKM通用機槍（卡拉什尼科夫機槍）", "PKM", ["pkm"], "轻机枪"),
    ("QJB-201輕機槍（二〇式輕機槍）", "QJB-201", ["QJB201", "qjb201", "201轻机枪"], "轻机枪"),
    ("M870霰彈槍", "M870", ["m870"], "霰弹枪"),
    ("M1014霰彈槍", "M1014", ["m1014"], "霰弹枪"),
    ("S12K霰彈槍", "S12K", ["s12k"], "霰弹枪"),
    ("725雙管霰彈槍（疑似滑膛槍）", "725", ["725双管"], "霰弹枪"),
    ("FS12霰彈槍", "FS12", ["fs12"], "霰弹枪"),
    ("SV-98狙擊步槍", "SV-98", ["SV98", "sv98"], "狙击步枪"),
    ("R93狙擊步槍", "R93", ["r93"], "狙击步枪"),
    ("M700狙擊步槍", "M700", ["m700"], "狙击步枪"),
    ("AWM狙擊步槍", "AWM", ["awm"], "狙击步枪"),
    ("M82狙擊步槍", "M82", ["巴雷特", "m82"], "狙击步枪"),
    ("複合弓", "复合弓", ["弓"], "特殊"),
]

# 人工核实的特性（来源聪聪，verified 只罩住 traits 字段的语义，整枪仍待核查槽位）
VERIFIED_TRAITS = {
    "腾龙": "特殊机制：开火稳定性收益显著，改装应着重堆叠「开火稳定性」属性（来源：bilibili Always聪聪）；另有额外爆头倍率加成（来源：萌娘百科）",
    "MK47": "特殊机制：拥有 1.25 倍「真实后坐力」收益，配件优先选择加真实后坐力的，收益会被放大（来源：bilibili Always聪聪）",
}

# 从百科「游戏内表现」里人工提炼的硬机制（影响改枪方向的才收）
WIKI_TRAITS = {
    "M4A1": "唯一可改装 M16A4 稳定枪托的武器：装托腮板后有双倍枪托精校上限，可获额外后坐力减免",
    "AKM": "全自动时有独特的准星下跳（而非上跳）；配件「性能枪管组合」将爆头倍率提至 2.5 倍并强化静止首发腰射，「实用长枪管组合」把下跳改回上跳",
    "MP5": "少数能把操纵速度拉满到 100 的枪，且是唯一能同时拉满腰射和操纵速度的枪",
    "Vector": "稳定性先天差：即使稳定性叠高，开镜准星仍会明显乱跳，只适合近距离",
    "MK4": "只有三连发与单发，无全自动模式",
    "M16A4": "只有三连发与单发，无全自动；绝活玩法是叠稳定性当三连发连狙打中远",
    "QJB-201": "弹道固定向左上跳，向右下匀速压枪即可；短枪管可消除机枪的扳机延迟；稳固导气几乎不用压枪，高速导气近战爆发但中距离描边",
    "M250": "唯一没有开镜随机散布的机枪；但扳机延迟 0.1 秒（其他机枪 0.05），必须提前枪",
    "AS Val": "S4 后坐力重制：开火产生巨大垂直和水平后坐力，但准星跳动几乎为零",
    "SR9": "连续射击到第 3 枪时后坐力大幅增加（你只有三枪的机会）；枪托套件不可拆，没有后握把和枪托槽位",
    "AWM": ".338 AP 弹为 7 级穿甲、无视防具；弹速最高 1200m/s，200m 内不用考虑下坠；枪声极大",
    "M82": "半自动重狙；.50 BMG 弹药禁止存入安全箱（死了全亏）；弹道下坠极其严重",
    "VSS": "自带消音+亚音速弹，枪声传播最短一档；但弹速慢被称「投石机」，中远要考虑下坠",
    ".357左轮": "有 0.1 秒扳机延迟；特色玩法「左轮狙」：狙击枪托+长枪管+高倍镜打中远（娱乐向）",
    "S12K": "唯一能装弹鼓+撞火枪托实现全自动的霰弹枪；龙息弹有严重视觉干扰",
    "M14": "开火瞬间大幅抬头，前五发极难压制；装「M14 先进枪身系统」后握把变手枪式握把、可改枪托（满改即 EBR）",
    "SKS": "裸枪护木无导轨、不能装前握把和战术配件；装「SKS 先进枪身系统」后可装 AKM 30 发弹匣且操控不降反增",
    "QCQ171": "独有击锤（枪机）配件：高速化枪机走高射速路线，稳固枪机走高稳定路线",
}


def split_sections(text: str) -> dict:
    """按 h4 枪名（繁）把全文切成 {标题: 该枪段落}"""
    titles = [t for t, *_ in ROSTER]
    sections = {}
    for i, title in enumerate(titles):
        start = text.find("\n" + title + "\n")
        if start < 0:
            start = text.find(title)
        if start < 0:
            continue
        end = len(text)
        for nxt in titles[i + 1:]:
            p = text.find("\n" + nxt, start + len(title))
            if p > 0:
                end = p
                break
        sections[title] = text[start:end]
    return sections


def parse_section(sec: str):
    """从一把枪的段落里抽：弹药、槽位列表、槽位备注、游戏内表现首句"""
    ammo = None
    m = re.search(r"使用彈藥[（?]*[：:](.+)", sec)
    if m:
        ammo = t2s(m.group(1).strip())

    slots, notes = [], []
    m = re.search(r"可改裝部分[：:](.+)", sec)
    if m:
        raw = m.group(1).strip()
        # 分号后面常是条件说明（如「装备部分枪管后可加装导轨与贴片」）
        if "；" in raw or ";" in raw:
            parts = re.split(r"[；;]", raw)
            raw = parts[0]
            for extra in parts[1:]:
                extra = t2s(extra.strip(" 。"))
                if extra:
                    notes.append(extra)
        for item in re.split(r"[、，,]", raw):
            item = item.strip()
            if not item:
                continue
            # 括号里的条件（如「装备后不可安装枪托和后握把」）挪到备注
            parens = re.findall(r"[（(]([^）)]*)[）)]", item)
            item = re.sub(r"[（(][^）)]*[）)]", "", item).strip()
            for p in parens:
                notes.append(f"{t2s(item)}：{t2s(p)}")
            item = t2s(item)
            if not item or item in ("等",):
                continue
            # 前后握把/左右导轨这种合并写法拆开，槽位才数得清
            if item == "前后握把":
                slots += ["前握把", "后握把"]
            elif item == "侧副瞄具":
                slots.append("侧瞄具")
            else:
                slots.append(item)

    m = re.search(r"獨特改[裝装]部分[：:](.+)", sec) or re.search(r"獨特配件[：:](.+)", sec)
    if m:
        notes.append("独特配件：" + t2s(m.group(1).strip().strip("。")))

    flavor = None
    m = re.search(r"遊戲內表現[：:](.+)", sec)
    if m:
        first = t2s(m.group(1).strip())
        first = first.split("。")[0].strip()
        if 6 <= len(first) <= 100:
            flavor = first + "。"
    return ammo, slots or None, notes, flavor


def main():
    raw = json.load(open(os.path.join(BASE, "data", "wiki_text.txt"), encoding="utf-8"))
    sections = split_sections(raw)

    profiles = {}
    missing = []
    for title, key, aliases, category in ROSTER:
        sec = sections.get(title, "")
        ammo, slots, notes, flavor = parse_section(sec)
        traits = VERIFIED_TRAITS.get(key) or WIKI_TRAITS.get(key)
        if not sec or slots is None:
            missing.append(key)
        profiles[key] = {
            "aliases": aliases,
            "category": category,
            "ammo": ammo,
            "slots": slots,
            "slot_notes": notes,
            "traits": traits,
            "notes": flavor,
            "builds": [],
            "source": "萌娘百科「三角洲行动/武器与配件」词条（2026-08-10 抓取）"
                      + ("+ bilibili Always聪聪" if key in VERIFIED_TRAITS else ""),
            "verified": False,
            "updated": "2026-08-10",
        }

    header = {
        "这是什么": "枪械档案库——人工核对过的地面真相，优先级高于联网搜索",
        "slots": "该枪可改装槽位清单（null=数据源未收录）；配件改变槽位的写在 slot_notes",
        "traits": "影响改枪方向的隐藏机制，方案必须围绕它展开",
        "notes": "一句话游戏内表现（风味信息，帮顾问说话像个玩家）",
        "verified": "true=玩家已核对可当事实用；false=待核对，模型引用时标（档案待核对）",
        "怎么纠错": "直接改这个文件保存即可，服务不用重启（热更新）",
    }
    out = {"_说明": header, **profiles}
    path = os.path.join(BASE, "data", "gun_profiles.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✅ 写入 {len(profiles)} 把枪 → {path}")
    print(f"⚠️  百科缺槽位数据的枪（slots=null）: {missing}")


if __name__ == "__main__":
    main()

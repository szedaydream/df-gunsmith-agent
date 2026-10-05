# -*- coding: utf-8 -*-
"""
聪聪改枪码 Excel → gun_profiles.json 的 builds 字段

数据源：data/聪聪/S10聪聪改枪码合集.xlsx.xlsx「聪聪排版优化改枪码」sheet
结构：第 25 行表头（武器名字/强度/改枪码/武器说明），之后：
  - 只有一个单元格的行 = 分类行（突击步枪/冲锋枪…）
  - 第一列有值的行 = 一把新枪（同行还有强度/第一个方案/武器说明）
  - 第一列为空的行 = 上一把枪的追加方案
改枪码列格式：「方案名-改枪码」，按最后一个 "-" 切。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
import openpyxl

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(BASE, "data", "聪聪", "S10聪聪改枪码合集.xlsx.xlsx")
PROFILES = os.path.join(BASE, "data", "gun_profiles.json")

# Excel 里的枪名 → 档案键名（对不上的手工补这里）
NAME_MAP = {
    "ASVAL": "AS Val", "AK12": "AK-12", "AKM": "AKM", "M4A1": "M4A1",
    "腾龙": "腾龙", "191": "腾龙", "QBZ191": "腾龙", "QBZ-191": "腾龙",
    "维克托": "Vector", "VECTOR": "Vector", "沙鹰": "沙漠之鹰",
    "95-1": "QBZ-95-1", "95": "QBZ-95-1", "QBZ95-1": "QBZ-95-1",
    "SCARH": "SCAR-H", "SCAR-H": "SCAR-H", "SR3M": "SR-3M",
    "QCQ171": "QCQ171", "杠杆步枪": "Marlin", "MARLIN": "Marlin",
    "巴雷特": "M82", "左轮": ".357左轮", "野牛": "野牛", "勇士": "勇士",
    "201": "QJB-201", "QJB201": "QJB-201",
    "95式突击步枪": "QBZ-95-1", "RM277（新枪）": "RM277",
    "SVCH（新枪）": "SVCH", "VSS慢波": "VSS", "Vector维克托": "Vector",
    "马林杠杆步枪": "Marlin",
}

CATEGORIES = {"突击步枪", "冲锋枪", "狙击步枪", "精确射手步枪", "射手步枪",
              "轻机枪", "霰弹枪", "手枪", "战斗步枪", "特殊"}


def norm(name: str) -> str:
    return re.sub(r"[\s\-]", "", str(name)).upper()


def main():
    profiles = json.load(open(PROFILES, encoding="utf-8"))
    guns = {k: v for k, v in profiles.items() if not k.startswith("_")}
    # 键名 + 别名都建索引，方便对 Excel 枪名
    index = {}
    for key, p in guns.items():
        index[norm(key)] = key
        for a in p.get("aliases", []):
            index[norm(a)] = key
    for k, v in NAME_MAP.items():
        index[norm(k)] = v

    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb["聪聪排版优化改枪码"]

    found = {}   # 档案键名 -> {"tier": str, "builds": [...], "review": str}
    unmatched = set()
    cur_gun = None
    # 实测列位：B=武器名字 G=强度 I=改枪码 P=武器说明（0 基索引 1/6/8/15）
    for row in ws.iter_rows(min_row=26, values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row]
        c0 = cells[1] if len(cells) > 1 else ""
        tier = cells[6] if len(cells) > 6 else ""
        codecell = cells[8] if len(cells) > 8 else ""
        review = cells[15] if len(cells) > 15 else ""
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        if len(nonempty) == 1 and nonempty[0] in CATEGORIES:
            cur_gun = None
            continue
        if c0:  # 新枪行
            key = index.get(norm(c0))
            if not key:
                unmatched.add(c0)
                cur_gun = None
                continue
            cur_gun = key
            found.setdefault(key, {"tier": tier, "builds": [], "review": ""})
            if tier:
                found[key]["tier"] = tier
            if review:
                found[key]["review"] = review.strip()
        if cur_gun and codecell and "-" in codecell:
            name, _, code = codecell.rpartition("-")
            found[cur_gun]["builds"].append(
                {"name": name.strip(), "code": code.strip(), "tier": tier})

    # 写回档案：builds 追加聪聪方案（不覆盖已有），强度/点评放 congcong 字段
    for key, data in found.items():
        p = guns[key]
        existing = {b.get("code") for b in p.get("builds", []) if isinstance(b, dict)}
        for b in data["builds"]:
            if b["code"] not in existing:
                p.setdefault("builds", []).append(b)
        p["congcong"] = {
            "tier": data["tier"], "season": "S10",
            "review": data["review"][:300],
        }
        if "Always聪聪" not in p.get("source", ""):
            p["source"] = p.get("source", "") + " + Always聪聪 S10 改枪码合集"

    with open(PROFILES, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)

    total_builds = sum(len(d["builds"]) for d in found.values())
    print(f"✅ 匹配 {len(found)} 把枪，写入 {total_builds} 个聪聪方案")
    print(f"⚠️  Excel 里没对上的枪名: {sorted(unmatched)}")
    no_build = [k for k in guns if k not in found]
    print(f"ℹ️  档案里没有聪聪方案的枪: {no_build}")


if __name__ == "__main__":
    main()

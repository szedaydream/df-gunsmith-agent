# -*- coding: utf-8 -*-
"""
生成档案核查清单：把 gun_profiles.json 整理成玩家友好的一份 markdown，
对着游戏逐把核对。核完一把：游戏里对一遍槽位 → 有错直接改 gun_profiles.json →
把该枪的 verified 改成 true → 清单里打勾。

跑法：python tests/build_checklist.py
输出：tests/档案核查清单.md
"""
import json, os, sys, datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = os.path.join(BASE, "data", "gun_profiles.json")
OUT = os.path.join(BASE, "tests", "档案核查清单.md")

CATEGORY_ORDER = ["突击步枪", "冲锋枪", "战斗步枪", "精确射手步枪", "射手步枪",
                  "狙击步枪", "轻机枪", "霰弹枪", "手枪", "特殊"]


def main():
    profiles = json.load(open(PROFILES, encoding="utf-8"))
    guns = {k: v for k, v in profiles.items() if not k.startswith("_")}

    by_cat = defaultdict(list)
    for name, p in guns.items():
        by_cat[p.get("category", "未知分类")].append((name, p))

    verified = sum(1 for p in guns.values() if p.get("verified"))
    no_slots = [k for k, p in guns.items() if not p.get("slots")]

    L = [f"# 枪械档案核查清单（{datetime.date.today().isoformat()} 生成）",
         "",
         f"共 {len(guns)} 把枪：已核查 {verified} / 缺槽位 {len(no_slots)}",
         "",
         "## 用法",
         "1. 从上到下对着游戏核对：重点看**槽位清单**（数量+名称）和**槽位备注**",
         "2. 有错直接改 `data/gun_profiles.json`（改完即生效，服务有热更新）",
         "3. 核对无误：把该枪的 `\"verified\": false` 改成 `true`，再把下面的 `[ ]` 打成 `[x]`",
         "4. wiki 小编黑话（「大人」「刮痧」等）顺手在 JSON 里清掉",
         "5. 全部核完后重跑本脚本，进度会刷新",
         ""]

    if no_slots:
        L += ["## ⚠️ 缺槽位数据，最优先（wiki 没收录这些枪的「可改装部分」）", ""]
        for name in no_slots:
            L.append(f"- [ ] **{name}**（{guns[name].get('category', '?')}）"
                     "—— 进游戏打开改枪界面，把槽位清单抄进 JSON 的 `slots` 数组")
        L.append("")

    for cat in CATEGORY_ORDER + sorted(set(by_cat) - set(CATEGORY_ORDER)):
        if cat not in by_cat:
            continue
        L.append(f"\n## {cat}\n")
        for name, p in by_cat[cat]:
            done = "x" if p.get("verified") else " "
            cc = p.get("congcong", {})
            tier = f"　聪聪评级 {cc.get('tier')}（{cc.get('season')}）" if cc else ""
            L.append(f"### [{done}] {name}{tier}\n")
            if p.get("aliases"):
                L.append(f"- 别名：{'、'.join(p['aliases'])}")
            if p.get("ammo"):
                L.append(f"- 弹药：{p['ammo']}")
            slots = p.get("slots")
            if slots:
                L.append(f"- 槽位（{len(slots)}）：{'、'.join(slots)}")
            else:
                L.append("- 槽位：**缺数据** ⚠️")
            for note in p.get("slot_notes", []):
                L.append(f"- 槽位备注：{note}")
            if p.get("traits"):
                L.append(f"- 特性：{p['traits']}")
            if p.get("notes"):
                L.append(f"- 实战印象：{p['notes']}")
            n_builds = len(p.get("builds", []))
            if n_builds:
                L.append(f"- 聪聪方案：{n_builds} 套")
            L.append("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"✅ 清单已生成: {OUT}")
    print(f"   {len(guns)} 把枪，已核查 {verified}，缺槽位 {len(no_slots)}: {no_slots}")


if __name__ == "__main__":
    main()

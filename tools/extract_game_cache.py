#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从《三角洲行动》客户端本地缓存中提取**真实**枪械 / 配件 / 改枪方案数据。

数据来源（游戏在「改枪」页面调用官方接口后落盘的 HTTP 响应缓存）：
    <游戏目录>/DeltaForce/Saved/Gamelet/cookies/getGunChangeSchemesolutionType_*

安全承诺：
    * 本脚本对游戏目录 **只读**（只 open(..., 'rb') / 列目录），绝不写入、删除、重命名。
    * 启动时与运行时各校验一次输出路径不在游戏目录内，防止误写。

用法：
    python tools/extract_game_cache.py                       # 用默认游戏目录
    python tools/extract_game_cache.py --game-dir "D:\\...\\DeltaForce(2001918)"
    python tools/extract_game_cache.py --out data/game       # 指定输出目录

产物（默认写到 data/game/）：
    items.json         全部道具（枪 + 配件）规范化后的统一字典
    guns.json          枪械（含官方真实数值）
    attachments.json   配件（含优点/缺点/数值修正）
    schemes.json       官方社区改枪方案（含改枪码）
    enrichment.json    与 data/gun_profiles.json 对照后的「可补全字段」建议
    extract_report.md  本次提取的可读报告
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import datetime

# --------------------------------------------------------------------------
# 默认路径
# --------------------------------------------------------------------------
DEFAULT_GAME_DIR = r"D:\sze\WeGameApps\rail_apps\DeltaForce(2001918)"
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "game")
PROFILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gun_profiles.json")

CACHE_SUBDIR = os.path.join("DeltaForce", "Saved", "Gamelet", "cookies")
CACHE_PREFIX = "getGunChangeScheme"

# 配件数值修正字段 -> 中文含义（用于生成可读数值）
NUMERIC_FIELDS = {
    "recoil": "后坐力",
    "controlSpeed": "操控速度",
    "controlStable": "稳定性",
    "hipShot": "腰射",
    "shotDistancePercent": "有效射程(%)",
    "bombCapacity": "弹匣容量/装药量",
}


# --------------------------------------------------------------------------
# 只读工具
# --------------------------------------------------------------------------
def assert_outside_game_dir(out_dir: str, game_dir: str) -> None:
    """确保输出目录不在游戏目录内 —— 这是本脚本最重要的一道保险。"""
    o = os.path.abspath(out_dir).lower().rstrip("\\/")
    g = os.path.abspath(game_dir).lower().rstrip("\\/")
    if o == g or o.startswith(g + os.sep):
        raise SystemExit(
            f"[拒绝执行] 输出目录位于游戏目录内部，可能破坏游戏文件：\n  输出: {o}\n  游戏: {g}"
        )


def read_json(path: str):
    """只读打开一个 UTF-8 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_payload(obj):
    """
    缓存文件有两种包裹层：
        {"iRet":0,...,"jData":{"data":{"code":0,"data":{...}}}}
        {"jData":{"data":{"code":0,"data":{...}}}}
    统一剥到含有 relateMap / list 的那一层。
    """
    seen = 0
    cur = obj
    while isinstance(cur, dict) and seen < 8:
        seen += 1
        if "relateMap" in cur or "list" in cur or "page" in cur:
            return cur
        nxt = None
        for k in ("jData", "data", "result", "ret"):
            if isinstance(cur.get(k), dict):
                nxt = cur[k]
                break
        if nxt is None:
            return None
        cur = nxt
    return None


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------
def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except ValueError:
            return None
    return None


def parse_effect_list(block) -> list:
    """把 advantage / disadvantage 里的 effectList 拍平成 [{text, value, color, condition}]"""
    out = []
    if not isinstance(block, dict):
        return out
    cond = block.get("condition")
    for e in block.get("effectList") or []:
        if not isinstance(e, dict):
            continue
        out.append({
            "text": e.get("value"),
            "value": _num(e.get("batteryValue")),
            "color": e.get("batteryColor"),
            "condition": cond,
        })
    return out


def normalize_item(raw: dict) -> dict:
    """把 relateMap 里的一条原始记录规范化成统一结构。"""
    acc = raw.get("accDetail") if isinstance(raw.get("accDetail"), dict) else None
    gun = raw.get("gunDetail") if isinstance(raw.get("gunDetail"), dict) else None

    item = {
        "objectID": raw.get("objectID"),
        "name": raw.get("objectName"),
        "kind": "gun" if gun else ("attachment" if acc else "other"),
        "primary_class": raw.get("primaryClass"),
        "slot_class": raw.get("secondClass"),
        "slot": raw.get("secondClassCN"),
        "grade": raw.get("grade"),
        "weight": _num(raw.get("weight")),
        "avg_price": raw.get("avgPrice"),
        "size": [raw.get("length"), raw.get("width")],
        "desc": raw.get("desc"),
        "pic": raw.get("pic"),
        "pre_pic": raw.get("prePic"),
    }

    if acc is not None:
        item["quick_separate"] = acc.get("quickSeparate")
        item["effects"] = {
            NUMERIC_FIELDS.get(k, k): v
            for k, v in acc.items()
            if k in NUMERIC_FIELDS and v is not None
        }
        item["pros"] = parse_effect_list(acc.get("advantage"))
        item["cons"] = parse_effect_list(acc.get("disadvantage"))

    if gun is not None:
        item["stats"] = {
            k: v for k, v in gun.items()
            if k not in ("accessory", "allAccessory", "ammo")
        }
        item["slot_ids"] = [a.get("slotID") for a in (gun.get("accessory") or []) if isinstance(a, dict)]
        item["ammo_ids"] = [a.get("objectID") for a in (gun.get("ammo") or []) if isinstance(a, dict)]

    return item


def normalize_scheme(raw: dict, gun_id: str | None) -> dict:
    s = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "solution_code": raw.get("solutionCode"),
        "solution_type": raw.get("solutionType"),
        "gun_id": gun_id,
        "author": (raw.get("authorDetail") or {}).get("nickname") or raw.get("authorNickname"),
        "author_channel": (raw.get("authorDetail") or {}).get("channel"),
        "comment": raw.get("authorComment"),
        "tags": [t.get("tagName") for t in (raw.get("tagDetail") or []) if isinstance(t, dict)],
        "apply_num": raw.get("applyNum"),
        "like_num": raw.get("likeNum"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "children": [],
    }
    for c in raw.get("childList") or []:
        if not isinstance(c, dict):
            continue
        ad = c.get("armsDetail") if isinstance(c.get("armsDetail"), dict) else {}
        gd = ad.get("gunDetail") if isinstance(ad.get("gunDetail"), dict) else {}
        s["children"].append({
            "name": c.get("name"),
            "solution_code": c.get("solutionCode") or None,
            "objectID": ad.get("objectID"),
            "object_name": ad.get("objectName"),
            "slot": ad.get("secondClassCN"),
            "grade": ad.get("grade"),
            "weight": _num(ad.get("weight")),
            "avg_price": ad.get("avgPrice"),
            "bd_price": c.get("bdPrice"),
            "stats": {k: v for k, v in gd.items() if k not in ("accessory", "allAccessory", "ammo")},
            "slot_ids": [a.get("slotID") for a in (gd.get("accessory") or []) if isinstance(a, dict)],
        })
    return s


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def extract(game_dir: str, out_dir: str) -> dict:
    cache_dir = os.path.join(game_dir, CACHE_SUBDIR)
    if not os.path.isdir(cache_dir):
        raise SystemExit(f"[找不到缓存目录] {cache_dir}\n请确认 --game-dir 指向 DeltaForce(2001918) 这一层。")

    assert_outside_game_dir(out_dir, game_dir)

    files = sorted(
        f for f in os.listdir(cache_dir)
        if f.startswith(CACHE_PREFIX) and not f.endswith(".meta")
    )

    items: dict = {}
    schemes: dict = {}
    bad_files: list = []
    src_files = 0

    for fn in files:
        path = os.path.join(cache_dir, fn)
        try:
            payload = find_payload(read_json(path))
        except Exception as e:                      # noqa: BLE001
            bad_files.append(f"{fn}: {e}")
            continue
        if not payload:
            bad_files.append(f"{fn}: 未能定位数据层")
            continue
        src_files += 1

        gun_id = None
        if "gunID_" in fn:
            try:
                gun_id = fn.split("gunID_")[1].split("_")[0]
            except IndexError:
                gun_id = None

        for _k, v in (payload.get("relateMap") or {}).items():
            if not isinstance(v, dict):
                continue
            oid = v.get("objectID") or _k
            if oid is None:
                continue
            # 同 ID 取信息量更大的那份
            cur = items.get(oid)
            new = normalize_item(v)
            if cur is None or (new.get("kind") != "other" and len(json.dumps(new)) >= len(json.dumps(cur))):
                items[oid] = new

        for raw in payload.get("list") or []:
            if not isinstance(raw, dict):
                continue
            key = raw.get("solutionCode") or raw.get("id")
            s = normalize_scheme(raw, gun_id)
            if key is None:
                continue
            cur = schemes.get(key)
            if cur is None or len(s["children"]) > len(cur["children"]):
                schemes[key] = s

    guns = {k: v for k, v in items.items() if v["kind"] == "gun"}
    accs = {k: v for k, v in items.items() if v["kind"] == "attachment"}

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "game_dir": os.path.abspath(game_dir),
        "cache_dir": cache_dir,
        "cache_files_total": len(files),
        "cache_files_parsed": src_files,
        "cache_files_failed": bad_files,
        "unique_items": len(items),
        "unique_guns": len(guns),
        "unique_attachments": len(accs),
        "unique_schemes": len(schemes),
    }

    os.makedirs(out_dir, exist_ok=True)
    _dump(os.path.join(out_dir, "items.json"), items)
    _dump(os.path.join(out_dir, "guns.json"), guns)
    _dump(os.path.join(out_dir, "attachments.json"), accs)
    _dump(os.path.join(out_dir, "schemes.json"), schemes)

    enrichment = build_enrichment(guns, accs, schemes)
    _dump(os.path.join(out_dir, "enrichment.json"), enrichment)

    with open(os.path.join(out_dir, "extract_report.md"), "w", encoding="utf-8") as f:
        f.write(render_report(report, guns, accs, schemes, enrichment))

    return report


def _dump(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------
# 与已有档案对照
# --------------------------------------------------------------------------
def build_enrichment(guns: dict, accs: dict, schemes: dict) -> dict:
    """
    把游戏真实数据按枪名对齐到 data/gun_profiles.json，
    只产出「建议补全」的内容，不修改原档案。
    """
    out = {"matched": {}, "profile_guns_without_game_data": [], "game_guns_not_in_profile": []}

    profiles = {}
    if os.path.isfile(PROFILE_PATH):
        try:
            profiles = {k: v for k, v in read_json(PROFILE_PATH).items() if k != "_说明"}
        except Exception:                            # noqa: BLE001
            profiles = {}

    # 名称 -> 游戏枪
    by_name = {}
    for g in guns.values():
        if g.get("name"):
            by_name[g["name"].lower()] = g

    all_slots = sorted({a.get("slot") for a in accs.values() if a.get("slot")})

    matched_game_ids = set()
    for pname, prof in profiles.items():
        cand = [pname.lower()] + [str(x).lower() for x in (prof.get("aliases") or [])]
        hit = next((by_name[c] for c in cand if c in by_name), None)
        if not hit:
            out["profile_guns_without_game_data"].append(pname)
            continue
        matched_game_ids.add(hit["objectID"])
        out["matched"][pname] = {
            "game_objectID": hit["objectID"],
            "game_slot_class": hit.get("slot"),
            "stats": hit.get("stats"),
            "slot_ids": hit.get("slot_ids"),
            "profile_slots": prof.get("slots"),
            "note": "游戏侧只提供槽位ID，槽位中文名需服务端下发；档案里的 slots 仍是人工整理",
            "all_attachment_slots_seen": all_slots,
        }
    for g in guns.values():
        if g["objectID"] not in matched_game_ids:
            out["game_guns_not_in_profile"].append({"name": g.get("name"), "objectID": g.get("objectID")})
    return out


def render_report(report, guns, accs, schemes, enrichment) -> str:
    L = []
    A = L.append
    A("# 游戏客户端真实数据提取报告\n")
    A(f"- 生成时间：{report['generated_at']}")
    A(f"- 游戏目录：`{report['game_dir']}`")
    A(f"- 缓存目录：`{report['cache_dir']}`")
    A(f"- 缓存文件：共 {report['cache_files_total']} 个，成功解析 {report['cache_files_parsed']} 个"
      + (f"，失败 {len(report['cache_files_failed'])} 个" if report["cache_files_failed"] else ""))
    A("")
    A("## 提取结果\n")
    A("| 数据 | 数量 |")
    A("|---|---|")
    A(f"| 唯一道具（枪 + 配件） | {report['unique_items']} |")
    A(f"| 枪械（含官方数值） | {report['unique_guns']} |")
    A(f"| 配件（含优缺点） | {report['unique_attachments']} |")
    A(f"| 官方社区改枪方案 | {report['unique_schemes']} |")
    A("")

    A("## 配件按槽位分布\n")
    cnt = collections.Counter(a.get("slot") for a in accs.values())
    A("| 槽位 | 配件数 |")
    A("|---|---|")
    for k, v in cnt.most_common():
        A(f"| {k} | {v} |")
    A("")

    A("## 枪械分类\n")
    gc = collections.Counter(g.get("slot") for g in guns.values())
    for k, v in gc.most_common():
        A(f"- {k}：{v} 把")
    A("")

    A("## 枪械官方数值样例（前 8 把）\n")
    A("| 枪名 | 类别 | 口径 | 肉伤 | 甲伤 | 后坐力 | 操控 | 稳定 | 腰射 | 射速 | 弹速 | 弹容 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for g in list(guns.values())[:8]:
        s = g.get("stats") or {}
        A(f"| {g.get('name')} | {g.get('slot')} | {s.get('caliber')} | {s.get('meatHarm')} | "
          f"{s.get('armorHarm')} | {s.get('recoil')} | {s.get('control')} | {s.get('stable')} | "
          f"{s.get('hipShot')} | {s.get('fireSpeed')} | {s.get('muzzleVelocity')} | {s.get('capacity')} |")
    A("")

    A("## 与 data/gun_profiles.json 的对照\n")
    A(f"- 档案中能对上游戏真实数据的枪：**{len(enrichment['matched'])}** 把")
    A(f"- 档案中有、但本地缓存还没覆盖到的枪：{len(enrichment['profile_guns_without_game_data'])} 把")
    A(f"- 游戏数据里有、但档案没收录的枪：{len(enrichment['game_guns_not_in_profile'])} 把")
    A("")
    if enrichment["profile_guns_without_game_data"]:
        A("**档案有、缓存没有的枪**（在游戏里打开这些枪的改枪页面即可补齐缓存）：\n")
        A("`" + "、".join(enrichment["profile_guns_without_game_data"]) + "`")
        A("")
    if enrichment["game_guns_not_in_profile"]:
        A("**游戏有、档案没收录的枪**：\n")
        for x in enrichment["game_guns_not_in_profile"]:
            A(f"- {x['name']}（{x['objectID']}）")
        A("")

    A("## 已知局限\n")
    A("1. **槽位中文名缺失**：接口只下发槽位 ID（如 `6`/`2`/`18`），不含中文名，")
    A("   因此无法自动校验档案里的 `slots` 是否正确。")
    A("2. **配件覆盖面取决于你浏览过哪些枪**：缓存是「你打开过改枪页面的枪」的并集；")
    A("   在游戏里多翻几把枪的改枪页，再跑一次本脚本，数据会自动变多。")
    A("3. **方案里的逐槽配件未下发**：`allAccessory` 只有 `slotID`，没有具体配件 objectID，")
    A("   所以方案只能拿到名称 + 改枪码，拿不到「哪个槽装了哪个件」的完整清单。")
    A("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="从三角洲行动客户端缓存提取真实枪械/配件数据（只读）")
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR, help="游戏根目录（含 DeltaForce 子目录的那一层）")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help="输出目录")
    args = ap.parse_args()

    report = extract(args.game_dir, args.out)
    print(f"[OK] 已解析缓存文件 {report['cache_files_parsed']}/{report['cache_files_total']}")
    print(f"[OK] 枪械 {report['unique_guns']} 把 | 配件 {report['unique_attachments']} 个 | "
          f"方案 {report['unique_schemes']} 套 | 道具合计 {report['unique_items']}")
    print(f"[OK] 输出目录: {os.path.abspath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

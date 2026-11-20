# -*- coding: utf-8 -*-
"""动作调度的**用户设置**。

为什么单独一个文件，而不是塞进 config.json：
    config.json 是运行状态（窗口位置、大小、语音开关），程序自己频繁写回。
    这里是人的偏好，只在用户改的时候写。混在一起，改一行排版会把你手写的
    说明冲掉。分开之后，`anim_config.json` 可以放心加注释字段、放心手改。

覆盖语义（照搬参考项目 dsh-pet 的做法）：
    内置 DEFAULTS 给全量默认值；用户在 anim_config.json 里写哪个字段就覆盖哪个，
    没写的回落默认。字段写错不报错、不崩，退回默认并在日志里说一声。
    整份文件删掉会重新生成一份带说明的完整版。

和 anim_manifest.py 的分工：
    anim_manifest 是「规则怎么算」，本文件是「规则用什么参数」。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "anim_config.json")

# 密度预设：只改两个间隔，别的都不动。
# 名字直白一点，菜单里就照这个显示。
DENSITIES = {
    "quiet":  {"label": "安静（少动）", "fidget_gap": [30, 70], "act_gap": [300, 600]},
    "normal": {"label": "正常", "fidget_gap": [10, 24], "act_gap": [90, 240]},
    "lively": {"label": "活跃", "fidget_gap": [6, 14], "act_gap": [40, 110]},
    "crazy":  {"label": "疯狂（很吵）", "fidget_gap": [3, 8], "act_gap": [15, 45]},
}

# 九个可调分类。键和 pet_acts.GROUPS 对齐，label 给菜单用。
GROUP_LABELS = [
    ("rest", "休息发呆"),
    ("eat", "吃东西"),
    ("play", "玩耍"),
    ("grind", "干活"),
    ("money", "钱与账"),
    ("proud", "得意表演"),
    ("impatient", "不耐烦"),
    ("whale", "大鲸鱼"),
    ("magic", "魔术把戏"),
]

DEFAULTS = {
    "_说明": [
        "大肥鱼的动作设置。改完保存，右键菜单「动作 → 重新读取设置」立即生效，不用重启。",
        "所有字段都可以不写；不写的用内置默认。整个文件删掉会重新生成一份。",
        "间隔单位是秒，写法 [最小, 最大]，每两次之间在这个范围里随机。",
        "group_scale 是各分类的倍率：1 = 正常，0 = 这类不演，2 = 出现频率翻倍。",
        "banned 里写动作名可以永久屏蔽它；only 非空时只演其中的动作。",
        "动作名就是 assets_anim_v2 下的目录名，右键「动作 → 这个不想看」会自动加进来。",
    ],

    # ---- 节奏 ----
    "density": "normal",           # quiet / normal / lively / crazy，仅作标签
    "fidget_gap": [10, 24],        # 小动作间隔（秒）
    "act_gap": [90, 240],          # 整段表演间隔（秒）

    # ---- 档位开关 ----
    "enable_fidget": True,         # 小动作（哈欠、张望、撸猫）
    "enable_act": True,            # 整段表演（吃、玩、乐器）
    "enable_fx": True,             # 大场面（放烟花、蓝鲸现世）
    "enable_festival": True,       # 节日动作（春节写福字、中秋月饼）
    "festival_chance": 0.55,       # 节日期间演节日动作的概率（0~1）

    # ---- 行为参数 ----
    "lonely_minutes": 25,          # 没人理多久开始「求关注」
    "cooldown": 36,                # 最近演过的 N 个不再重演（防翻来覆去那几个）
    "time_of_day": True,           # 时段加权：白天偏干活、夜里偏休息

    # ---- 分类倍率 ----
    "group_scale": {k: 1.0 for k, _ in GROUP_LABELS},

    # ---- 白/黑名单 ----
    "banned": [],
    "only": [],
}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _gap(v):
    return (isinstance(v, (list, tuple)) and len(v) == 2
            and all(_num(x) for x in v) and v[0] <= v[1] and v[0] >= 0)


def normalize(raw):
    """把用户写的和默认的合起来，顺手把明显写错的字段换回默认值。

    不抛异常——桌宠因为配置文件里一个错字起不来，是最糟糕的处理方式。
    """
    cfg = {}
    for k, v in DEFAULTS.items():
        cfg[k] = dict(v) if isinstance(v, dict) else (
            list(v) if isinstance(v, list) else v)
    if not isinstance(raw, dict):
        return cfg

    bad = []

    # 密度：先应用预设，再让显式写的 gap 覆盖（写什么听什么）
    d = raw.get("density")
    if isinstance(d, str) and d in DENSITIES:
        cfg["density"] = d
        cfg["fidget_gap"] = list(DENSITIES[d]["fidget_gap"])
        cfg["act_gap"] = list(DENSITIES[d]["act_gap"])
    elif d is not None:
        bad.append("density")

    for key in ("fidget_gap", "act_gap"):
        if key in raw:
            if _gap(raw[key]):
                cfg[key] = [max(0.5, float(raw[key][0])),
                            max(0.5, float(raw[key][1]))]
            else:
                bad.append(key)

    for key in ("enable_fidget", "enable_act", "enable_fx",
                "enable_festival", "time_of_day"):
        if key in raw:
            if isinstance(raw[key], bool):
                cfg[key] = raw[key]
            else:
                bad.append(key)

    if "festival_chance" in raw:
        v = raw["festival_chance"]
        if _num(v) and 0 <= v <= 1:
            cfg["festival_chance"] = float(v)
        else:
            bad.append("festival_chance")

    for key, lo, hi in (("lonely_minutes", 1, 600),
                        ("cooldown", 0, 200)):
        if key in raw:
            v = raw[key]
            if _num(v) and lo <= v <= hi:
                cfg[key] = int(v)
            else:
                bad.append(key)

    gs = raw.get("group_scale")
    if isinstance(gs, dict):
        for k, v in gs.items():
            if k in cfg["group_scale"] and _num(v) and 0 <= v <= 10:
                cfg["group_scale"][k] = float(v)
            elif k in cfg["group_scale"]:
                bad.append("group_scale." + k)

    for key in ("banned", "only"):
        if key in raw:
            v = raw[key]
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                cfg[key] = list(v)
            else:
                bad.append(key)

    cfg["_bad"] = bad
    return cfg


def load(path=PATH, quiet=False):
    """读设置。文件不在就照默认值生成一份（带说明，方便人手改）。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            cfg = normalize(raw)
            if cfg["_bad"] and not quiet:
                print(f"[anim] 这些字段写得不对，已用默认值：{cfg['_bad']}")
            return cfg
        except Exception as e:
            print(f"[anim] 设置读不了（{e}），先按默认跑")
            return normalize(None)
    cfg = normalize(None)
    cfg.pop("_bad", None)
    save(cfg, path, quiet=True)
    return cfg


def save(cfg, path=PATH, quiet=False):
    """写回用户设置。内部字段（_bad）不落盘。"""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        if not quiet:
            print(f"[anim] 设置存不下来：{e}")
        return False


def set_density(cfg, key):
    """菜单里切密度：只改间隔，别的字段原样保留。"""
    if key not in DENSITIES:
        return cfg
    cfg["density"] = key
    cfg["fidget_gap"] = list(DENSITIES[key]["fidget_gap"])
    cfg["act_gap"] = list(DENSITIES[key]["act_gap"])
    return cfg


def describe(cfg):
    """一句话概括当前设置，菜单顶上显示。"""
    d = DENSITIES.get(cfg.get("density", "normal"), DENSITIES["normal"])
    on = sum(1 for k, _ in GROUP_LABELS
             if cfg["group_scale"].get(k, 1) > 0)
    return (f"{d['label']}·小动作{int(cfg['fidget_gap'][0])}-"
            f"{int(cfg['fidget_gap'][1])}s·表演{int(cfg['act_gap'][0])}-"
            f"{int(cfg['act_gap'][1])}s·{on}类动作")

# -*- coding: utf-8 -*-
"""109 个动作的注册表 + 调度规则。

和 pet_acts.py 的分工：
- **pet_acts** 管「性格」——说什么话、按什么分组、几点偏什么。
- **anim_manifest**（本文件）管「调度」——这一秒该不该演、演哪一档。

为什么要把动作分档，而不是一个权重池随机抽：
    109 个动作里，「东张西望」和「放烟花」是两种东西。前者是待机时的小动作，
    两三秒就过，不打断你干活；后者是整段表演，一演九秒，眼睛会被拽过去。
    混在一个池子里抽，结果就是「刚打了个哈欠，下一秒开始放烟花」——乱。

三档：
    fidget  小动作，频繁，低干扰（哈欠、张望、撸猫、敲桌）
    act     整段表演，稀疏（吃、玩、乐器、节令、工作状态）
    fx      大场面，更稀疏（放烟花、蓝鲸现世、变鸽子）

另外两类不走调度，由事件驱动：
    reaction  被点、被拖、被吓（pet.py 直接调）
    state     待机循环、睡觉三段（状态机管）
"""
from __future__ import annotations

import random

import pet_acts
from pet_acts import GROUPS, group_of, phase_of, weights_for

FIDGET = "fidget"
ACT = "act"
FX = "fx"
REACTION = "reaction"
STATE = "state"

# ---------------------------------------------------------------- 分类
# 只列需要特别指定的，剩下的按「不在 FIDGET / FX / REACTION / STATE 里就是 act」。
_FIDGET = frozenset({
    "peek", "yawn", "stretch", "doze", "hum", "mirror", "cat", "brush",
    "mumble_daze", "mumble_screen", "mumble_wiping",
    "tap_desk", "tail_slap", "float_step", "nap", "crouch", "spin", "run",
    "walk", "jump",
})

# 大场面：好看但耗神，权重压低，冷却拉长
_FX = frozenset({
    "whale", "bubbles", "animals", "butterfly", "fireworks",
    "ghost", "pigeon", "magic_flower",
})

_REACTION = frozenset({
    "click_wave", "click_angry", "click_shy", "click_happy", "click_tickle",
    "startle", "drag",
})

_STATE = frozenset({"idle", "sleep_in", "sleep_loop", "sleep_out"})


def kind_of(name):
    if name in _STATE:
        return STATE
    if name in _REACTION:
        return REACTION
    if name in _FIDGET:
        return FIDGET
    if name in _FX:
        return FX
    return ACT


# ---------------------------------------------------------------- 节令
# 这些动作只在对应节日出现，不该在 7 月随机演「圣诞树」。
# 它们 kind 是 act/fx，所以要在调度里单独挡住——见 _season_only()。
_SEASON_ONLY = frozenset(
    pet_acts.GROUPS.get("festival", []))


def season_only(name):
    return name in _SEASON_ONLY


# (起月, 起日, 止月, 止日, 标签, 候选动作)。跨年的区间把起日写大就行。
FESTIVALS = [
    ((1, 1), (1, 6), "元旦", ["fireworks", "write_fu", "redpacket"]),
    ((1, 10), (2, 20), "春节", ["write_fu", "lion_dance", "redpacket",
                              "eat_dumpling", "eat_niangao"]),
    ((2, 1), (2, 20), "元宵", ["eat_tangyuan", "lantern"]),
    ((4, 1), (4, 12), "清明", ["eat_qingtuan", "kite"]),
    ((5, 20), (6, 25), "端午", ["eat_zongzi", "shuttlecock"]),
    ((7, 20), (8, 10), "七夕", ["needle", "river_lantern"]),
    ((8, 20), (9, 25), "中秋", ["mooncake", "lantern"]),
    ((9, 25), (10, 12), "重阳", ["eat_chongyang", "chrysanthemum"]),
    ((10, 20), (11, 5), "万圣", ["pumpkin", "ghost"]),
    ((12, 15), (12, 31), "圣诞", ["xmas_tree", "gift", "snowman"]),
    ((12, 20), (1, 5), "跨年", ["snowman", "fireworks", "gift"]),
]


def _in_range(md, a, b):
    if a <= b:
        return a <= md <= b
    return md >= a or md <= b          # 跨年（12 月 → 1 月）


def festival_of(month, day):
    """今天是什么节气/节日。命中多个就随机一个，没有就 None。"""
    hits = [f for f in FESTIVALS if _in_range((month, day), f[0], f[1])]
    if not hits:
        return None
    return random.choice(hits)


# ---------------------------------------------------------------- 冷却
class Cooldown:
    """最近演过的不再演。

    没有这一层，109 个动作也会出现「连着三次都在撸猫」——
    随机抽样的重复率比人直觉里高得多。
    """

    def __init__(self, size=36):
        self.size = size
        self.seen = []

    def hit(self, name):
        self.seen.append(name)
        if len(self.seen) > self.size:
            self.seen = self.seen[-self.size:]

    def filter(self, names):
        cand = [n for n in names if n not in self.seen]
        return cand or list(names)         # 全被冷却了就放开，别死锁


# ---------------------------------------------------------------- 调度
def _usable(available, cfg):
    """先按用户的白/黑名单过一遍，再谈权重。

    黑名单比权重硬：写了 banned 就是不想看，不该靠「权重压到最低」来糊过去。
    白名单非空则反过来只保留其中的动作——这是「我只想让它演这几个」的用法。
    """
    if not cfg:
        return list(available)
    names = list(available)
    only = cfg.get("only") or []
    if only:
        names = [n for n in names if n in only]
    banned = set(cfg.get("banned") or [])
    if banned:
        names = [n for n in names if n not in banned]
    return names


def _scale(cfg, g, default=1.0):
    """分类倍率。0 = 这类全关，2 = 翻倍。没设置就是 1。"""
    if not cfg:
        return default
    try:
        return float(cfg.get("group_scale", {}).get(g, default))
    except (TypeError, ValueError):
        return default


def _pool(kinds, available, cfg=None):
    return [n for n in _usable(available, cfg) if kind_of(n) in kinds]


def pick_fidget(hour, available, cd=None, last_group=None, cfg=None):
    """待机小动作。按组加权，但只在 fidget 里挑。"""
    if cfg and not cfg.get("enable_fidget", True):
        return None, None
    cand = _pool({FIDGET}, available, cfg)
    if not cand:
        return None, None
    if cd:
        cand = cd.filter(cand)
    w = weights_for(hour, time_of_day=cfg.get("time_of_day", True)
                    if cfg else True)
    groups = {group_of(n) for n in cand} - {None}
    if not groups:
        return random.choice(cand), None
    # 刚演过的那一组这次降权（不是禁用，只是别连着来）
    gw = {}
    for g in groups:
        k = _scale(cfg, g)
        if k <= 0:                      # 这一类被关掉了，直接不给机会
            continue
        gw[g] = w.get(g, 8) * k
    if not gw:                          # 全关光了，别硬撑
        return None, None
    if last_group in gw:
        gw[last_group] = max(0.5, gw[last_group] * 0.35)
    g = random.choices(list(gw), weights=[gw[k] for k in gw], k=1)[0]
    in_g = [n for n in cand if group_of(n) == g]
    return random.choice(in_g or cand), g


def pick_act(hour, month, day, available, cd=None, last_group=None,
             minutes_idle=0.0, cfg=None):
    """整段表演。节令优先，然后按时段加权。"""
    if cfg and not cfg.get("enable_act", True):
        return None, None

    kinds = {ACT}
    if not cfg or cfg.get("enable_fx", True):
        kinds.add(FX)                   # 关掉大场面就只从普通表演里挑
    cand = _pool(kinds, available, cfg)
    if not cand:
        return None, None

    usable = set(_usable(available, cfg))

    # 节令：命中就大概率演，一年就那么几天，错过就没了
    fest = festival_of(month, day) if (
        not cfg or cfg.get("enable_festival", True)) else None
    if fest:
        chance = float(cfg.get("festival_chance", 0.55)) if cfg else 0.55
        if random.random() < chance:
            pick = [n for n in fest[2] if n in usable]
            if pick:
                return random.choice(pick), "festival:" + fest[2][0]

    # 节令动作（月饼、圣诞树、放烟花这类）只在命中节日时上面才放行；
    # 平时从普通池里挡掉，否则 7 月也能随机抽到「圣诞树」。
    # 例外：放烟花这类 fx 即使不是节日也能作为大场面偶尔出现——
    # 所以只挡「纯节令」的 act，不挡 fx。
    if not fest:
        cand = [n for n in cand
                if not season_only(n) or kind_of(n) == FX]

    if cd:
        cand = cd.filter(cand)

    # 没人理太久：演个「想要注意」的，而不是自顾自吃饭
    lonely_after = float(cfg.get("lonely_minutes", 25)) if cfg else 25
    if lonely_after > 0 and minutes_idle > lonely_after and random.random() < 0.4:
        pick = [n for n in ("work_pace", "mumble_screen", "tap_desk",
                            "work_sigh") if n in usable]
        if pick:
            return random.choice(pick), "lonely"

    w = weights_for(hour, time_of_day=cfg.get("time_of_day", True)
                    if cfg else True)
    groups = {group_of(n) for n in cand} - {None}
    if not groups:
        return random.choice(cand), None
    gw = {}
    for g in groups:
        k = _scale(cfg, g)
        if k <= 0:
            continue
        base = w.get(g, 8)
        # fx 类压低：好看不常有才好看
        gw[g] = base * k * (0.45 if g == "whale" else 1.0)
    if not gw:
        return None, None
    if last_group in gw:
        gw[last_group] = max(0.5, gw[last_group] * 0.3)
    g = random.choices(list(gw), weights=[gw[k] for k in gw], k=1)[0]
    in_g = [n for n in cand if group_of(n) == g]
    # 组内如果既有 act 又有 fx，优先 act（fx 靠组权重压着，别在组内又占一半）
    plain = [n for n in in_g if kind_of(n) == ACT]
    if plain and random.random() < 0.75:
        in_g = plain
    return random.choice(in_g or cand), g


def pick_wake(hour, available):
    """被点醒/被理睬时的小反应，走 reaction 之外的轻动作。"""
    w = [("peek", 30), ("click_wave", 24), ("yawn", 16), ("stretch", 12)]
    cand = [(n, p) for n, p in w if n in available]
    if not cand:
        return None
    return random.choices([n for n, _ in cand],
                          weights=[p for _, p in cand], k=1)[0]


def summary(available):
    """自检用：每档各有多少个。"""
    out = {STATE: 0, REACTION: 0, FIDGET: 0, ACT: 0, FX: 0}
    for n in available:
        out[kind_of(n)] += 1
    return out

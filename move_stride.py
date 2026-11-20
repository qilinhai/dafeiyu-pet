# -*- coding: utf-8 -*-
"""移动动作的步幅标定：让「腿在动」和「身体在走」对得上，不打滑。

问题：桌宠朝目标平移时只是贴图滑过去，脚一动不动（或者播着动画但速度随便给），
看起来像被人推着走的纸片。

办法：每段移动动画标一个「一圈走多远」的数，位移由这个数推进：
    每秒位移 = 一圈步幅 ÷ 一圈时长（有效帧区间）× 播放倍率
播放倍率由「想要的速度 ÷ 自然步速」算出来，夹在 [min_rate, max_rate] 里。
这样腿的摆动和地面的移动是同一个时钟，就不会打滑。

数据来源：上游 dsh-pet 的 move_strides.json（2026-09-19 用鞋簇/触点跟踪标定，
@360px 高：左转奔跑 240 / 螃蟹走路 220 / 漂浮踏步 90）。这里换算成
「相对角色身高的倍数」并存进本地 JSON，改数值不用改代码。

为什么还要 boost（步幅放大）：
    素材本身是慢动作——螃蟹走路一圈 8 秒才挪 220px（@360 高），按原速走
    鱼会变成蜗牛。boost 承认「这条鱼一步跨得远」，把一圈步幅放大到可用区间，
    腿和地面依旧同一个时钟，只是步子大。改 boost 只影响观感，不会让脚打滑。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "move_strides.json")

# 文件丢了也照常工作：数值和出厂 JSON 逐字一致，行为不跳变。
DEFAULT = {
    "speed": 170.0,
    "max_rate": 3.0,
    "min_rate": 0.8,
    "dirs": {"left": "crab_walk", "right": "crab_walk",
             "up": "float_step", "down": "float_step"},
    "clips": {
        "crab_walk": {"stride_ratio": 0.611, "boost": 2.5, "active": [0.09, 0.88]},
        "float_step": {"stride_ratio": 0.25, "boost": 4.0, "active": [0.04, 0.99]},
        "run": {"stride_ratio": 0.667, "boost": 2.5, "active": [0.23, 0.51]},
    },
}

_cache = {"stamp": None, "cfg": None}


def _stamp():
    try:
        st = os.stat(CFG_PATH)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load(force=False):
    """读配置（按 mtime 热加载）。坏文件一律退回默认值，不让桌宠起不来。"""
    stamp = _stamp()
    if not force and _cache["cfg"] is not None and _cache["stamp"] == stamp:
        return _cache["cfg"]
    cfg = json.loads(json.dumps(DEFAULT))          # 深拷贝默认值
    if stamp is not None:
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in (raw or {}).items():
                if k == "clips" and isinstance(v, dict):
                    cfg["clips"].update(v)
                elif k != "_comment":
                    cfg[k] = v
        except Exception as e:
            print("[move] move_strides.json 读不了，用默认值:", e)
    _cache["cfg"] = cfg
    _cache["stamp"] = stamp
    return cfg


def ensure_file():
    """没这个文件就写一份出厂值，方便用户直接改。"""
    if os.path.exists(CFG_PATH):
        return False
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print("[move] 写 move_strides.json 失败:", e)
        return False


def clip_for(direction, cfg=None):
    """朝向 → 移动动作名。left/right 用侧向走的，上下用漂浮踏步。"""
    cfg = cfg or load()
    return (cfg.get("dirs") or {}).get(direction)


def timing(clip, seconds, height, cfg=None):
    """算这段移动动画该怎么播、走多快。

    返回 {"span": (f0, f1), "rate": r, "speed": px/s, "stride": px}；
    没有这段的标定数据就返回 None（调用方退回原来的贴图平移）。
    """
    cfg = cfg or load()
    spec = (cfg.get("clips") or {}).get(clip)
    if not spec:
        return None
    try:
        ratio = float(spec["stride_ratio"])
        boost = float(spec.get("boost", 1.0))
        a, b = spec.get("active") or (0.0, 1.0)
        f0 = max(0.0, min(0.99, float(a)))
        f1 = max(f0 + 0.01, min(1.0, float(b)))
        target = float(cfg.get("speed", 170.0))
        hi = float(cfg.get("max_rate", 3.0))
        lo = float(cfg.get("min_rate", 0.8))
    except (KeyError, TypeError, ValueError):
        return None

    span_s = max(0.2, (f1 - f0) * max(0.2, float(seconds)))
    stride_px = ratio * float(height) * boost
    natural = stride_px / span_s                 # 素材的自然步速 px/s
    rate = max(lo, min(hi, target / natural)) if natural > 0 else 1.0
    return {"span": (f0, f1), "rate": rate, "speed": natural * rate,
            "stride": stride_px}

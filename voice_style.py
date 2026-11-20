# -*- coding: utf-8 -*-
"""大肥鱼 · 念法层：同一个音色，按场景换「怎么念」。

背景（2026-11-16 实测，真机生成后转回文字看时间戳）：

    现状  招商银行到账3200元，进得快，出得更快。   0 → 5.55s，一整段没断点
    C 方案 到账3200元。[停顿] 进得快…             3.00s ｜ 静默 0.94s ｜ 1.93s

普通句号在 TTS 里几乎不停（会被当成逗号级连接），所以「梗」永远黏在事实上，
出来的是「念完账目顺口补一句」，不是「损你一句」。这层解决两件事：

1. **停顿标签**：在事实和尾巴之间插一个真的断句（`[停顿]` / `[啧嘴]` / `[叹气]`）。
   实测标签不会被念成字，`[叹气]` 会变成真实的叹气声。
2. **表演指令**：每类一句中文 instructions + guidance_scale（1–3 是官方建议的
   表现力区间）。不加的话，API 只能用默认甜腔把毒舌台词念到底。

紧急类刻意不插标签：实测中间静默 1.57 秒，反而不像警报。

设计边界：
- 这一层只管「怎么念」，不管「说什么」。台词还是 notify_voice 的事。
- 气泡显示的是干净文本，只有念出来那份带标签（调用方各拿一份）。
- 配置外置成 voice_style.json，改完不用重启（按 mtime 热加载）。
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "voice_style.json")

# 官方区间：guidance_scale 1.0–10.0，默认 1.0。表现力场景从 1–3 起试。
GUIDANCE_MIN, GUIDANCE_MAX = 1.0, 10.0

# 每类一句表演指令。写中文（与 language_code 一致），只说 2–3 个决策：
# 情境 / 意图 / 情绪强度 / 节奏。不写「你是XX」这种换身份的话。
DEFAULTS = {
    "enabled": True,
    "language": "zh",          # zh=走 breeze-tts-2 专用中文模型；空=自动（多语言模型）
    "styles": {
        "money": {
            "guidance": 2.5, "tag": "[停顿]",
            "instructions": "像店主自己嘟囔账目，语气轻松带点嫌弃，别念得像播报。",
        },
        "chat": {
            "guidance": 3.0, "tag": "[啧嘴]",
            "instructions": "像熟人随口损一句，有点不耐烦，语速偏快，不卖萌。",
        },
        "work": {
            "guidance": 2.5, "tag": "[叹气]",
            "instructions": "一边忙一边说，带点累，语气平实不煽情。",
        },
        "tech": {
            "guidance": 2.5, "tag": "[叹气]",
            "instructions": "一边修一边吐槽，不太耐烦。",
        },
        "flood": {
            "guidance": 3.0, "tag": "",
            "instructions": "不耐烦，语速快，像被打扰了。",
        },
        "phone": {
            "guidance": 2.0, "tag": "[叹气]",
            "instructions": "有点急，像在催人。",
        },
        "phone_back": {
            "guidance": 1.5, "tag": "",
            "instructions": "轻松，松一口气。",
        },
        "notice": {
            "guidance": 2.0, "tag": "",
            "instructions": "随口一提，不郑重，像顺嘴说一句。",
        },
        # 紧急不插标签：实测 [停顿] 会留 1.5 秒静默，那种「卡住感」不像警报。
        "urgent": {
            "guidance": 2.0, "tag": "",
            "instructions": "短促咬紧，声音沉一点，不拖长音，不卖萌。",
        },
    },
    # 没配的类别走这个
    "fallback": {
        "guidance": 2.0, "tag": "",
        "instructions": "像随口说一句，别念得像播报。",
    },
}

# 标点：插标签时要跳过事实末尾那串句读
_LEAD = re.compile(r"^[。！？!?，,、：:；;…\s]*")

_CFG = None
_MTIME = None


def _stamp():
    try:
        st = os.stat(CFG_PATH)
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def load(force=False):
    """读配置。文件不在/坏了就用 DEFAULTS —— 行为不能因为少个文件就崩。"""
    global _CFG, _MTIME
    cur = _stamp()
    if not force and _CFG is not None and cur == _MTIME:
        return _CFG
    cfg = None
    if cur is not None:
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                cfg = _merge(raw)
        except Exception:
            cfg = None
    _CFG, _MTIME = (cfg or json.loads(json.dumps(DEFAULTS))), cur
    return _CFG


def _merge(raw):
    """浅合并：用户在文件里只写想改的那几类也行，其余照默认。"""
    out = json.loads(json.dumps(DEFAULTS))
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    if "language" in raw:
        out["language"] = str(raw.get("language") or "")
    for k in ("fallback",):
        if isinstance(raw.get(k), dict):
            out[k].update({kk: raw[k][kk] for kk in raw[k]})
    styles = raw.get("styles")
    if isinstance(styles, dict):
        for kind, val in styles.items():
            if not isinstance(val, dict):
                out["styles"][kind] = val
                continue
            base = dict(out["styles"].get(kind) or out.get("fallback") or {})
            base.update(val)
            out["styles"][kind] = base
    return out


def style_for(kind):
    """某一类的念法。没配就回落，保证永远有值可传。"""
    cfg = load()
    st = (cfg.get("styles") or {}).get(kind)
    if not isinstance(st, dict):
        st = cfg.get("fallback") or DEFAULTS["fallback"]
    g = st.get("guidance", 2.0)
    try:
        g = float(g)
    except (TypeError, ValueError):
        g = 2.0
    g = min(max(g, GUIDANCE_MIN), GUIDANCE_MAX)
    return {
        "guidance": g,
        "tag": str(st.get("tag") or ""),
        "instructions": str(st.get("instructions") or "").strip(),
    }


def language():
    return str(load().get("language") or "")


def enabled():
    return bool(load().get("enabled", True))


def tts_text(line, fact, kind):
    """念出来的那一份：在「事实」和「尾巴」之间插一个真的断句。

    气泡显示的是原样的 line，这一份只给 TTS。三个前提不满足就原样返回：
    总开关关着、这类没配标签、事实和整句对不上（宁可不断，也别断错地方）。
    """
    line = line or ""
    fact = (fact or "").strip("，。、 　")
    if not line or not fact or not enabled():
        return line
    st = style_for(kind)
    tag = st.get("tag") or ""
    if not tag or not line.startswith(fact):
        return line
    rest = line[len(fact):]
    lead = _LEAD.match(rest).group(0)
    tail = rest[len(lead):]
    if not tail:                       # 只有事实、没有尾巴，插了也没意义
        return line
    return f"{fact}{lead}{tag}{tail}"


def save(cfg):
    """给面板用：整份写回。写坏了下次读取会回默认，不会把鱼弄哑。"""
    global _CFG, _MTIME
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)
    _CFG, _MTIME = None, None
    return load(force=True)


def ensure_file():
    """第一次跑起来时把默认配置落盘，方便用户直接改。已存在就不动。"""
    if os.path.exists(CFG_PATH):
        return False
    try:
        save(json.loads(json.dumps(DEFAULTS)))
        return True
    except Exception:
        return False


if __name__ == "__main__":
    ensure_file()
    print("配置:", CFG_PATH)
    for k in DEFAULTS["styles"]:
        s = style_for(k)
        print(f"  {k:11s} g={s['guidance']:<4} tag={s['tag'] or '—':5s} {s['instructions'][:28]}")

# -*- coding: utf-8 -*-
"""桌宠提示音：该提醒但不该出人声的时候，用一声「叮」代替。

为什么自己合成 wav，而不是 winsound.Beep 或系统提示音：

- Beep 是阻塞调用，会把桌宠的动画 tick 卡住 0.2 秒；音色还由蜂鸣器/声卡决定，
  有的机器听起来像报错，不像提醒。
- 系统提示音（SystemAsterisk 等）跟着「声音方案」变，用户一关系统音效就没了。

所以这里合成短音，落盘到临时目录，用 SND_ASYNC 播：零阻塞、音色固定、
不依赖系统声音方案。

**模板和音量都在 sound_config.json 里，设置面板「运行 → 提示音」直接改**，
本模块每次播放前比对文件 mtime 热加载，改完不用重启桌宠。

音量上限留了 0.95，因为这类提示经常是在手机麦克风已经打开的时候响的，
响太大声会被那头录进去。
"""
from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "sound_config.json")

_RATE = 22050
_TMP = tempfile.gettempdir()

DEFAULT_CFG = {
    "_note": "提示音配置。不出人声时（比如叫悠悠）播这个。改完即时生效，不用重启。",
    "enabled": True,
    "template": "ding",
    "volume": 0.7,
    "custom_path": "",
}

# 模板：key → (面板显示名, 合成参数)
# tones 里每项是（频率Hz, 振幅）；seq=True 表示几个音依次响，False 表示叠成一个和弦
TEMPLATES = {
    "ding": ("叮 · 玻璃双音", dict(dur=0.30, decay=11.0, seq=False,
                             tones=((1318.5, 0.30), (1975.5, 0.14)))),
    "beep": ("滴 · 短促单音", dict(dur=0.12, decay=26.0, seq=False,
                             tones=((1046.5, 0.45),))),
    "chime": ("叮咚 · 两连音", dict(dur=0.52, decay=8.0, seq=True,
                              tones=((1046.5, 0.30), (1568.0, 0.22)))),
    "soft": ("柔音 · 环境感", dict(dur=0.38, decay=6.5, seq=False,
                              tones=((880.0, 0.26), (1320.0, 0.09)))),
    "custom": ("自定义 wav 文件", None),
}

_cfg = None
_cfg_mtime = None


def load_cfg(force=False):
    """读配置，按 mtime 缓存。文件坏了/缺了退回默认，不抛异常。"""
    global _cfg, _cfg_mtime
    try:
        m = os.path.getmtime(CFG_PATH)
    except OSError:
        _cfg, _cfg_mtime = dict(DEFAULT_CFG), None
        return _cfg
    if not force and _cfg is not None and m == _cfg_mtime:
        return _cfg
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = dict(DEFAULT_CFG)
        cfg.update(raw if isinstance(raw, dict) else {})
        if cfg.get("template") not in TEMPLATES:
            cfg["template"] = "ding"
        cfg["volume"] = round(max(0.05, min(0.95, float(cfg.get("volume", 0.7)))), 2)
        cfg["enabled"] = bool(cfg.get("enabled", True))
    except Exception as e:
        print("[ding] 配置读不了，用默认:", e)
        cfg = dict(DEFAULT_CFG)
    _cfg, _cfg_mtime = cfg, m
    return cfg


def save_cfg(cfg):
    """面板保存用。只认已知字段，顺手把类型和范围收紧。"""
    global _cfg, _cfg_mtime
    out = dict(DEFAULT_CFG)
    out["enabled"] = bool(cfg.get("enabled", True))
    tpl = cfg.get("template")
    out["template"] = tpl if tpl in TEMPLATES else "ding"
    out["volume"] = round(max(0.05, min(0.95, float(cfg.get("volume", 0.7)))), 2)
    out["custom_path"] = str(cfg.get("custom_path") or "")
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    _cfg, _cfg_mtime = None, None
    return out


def _sample(i, dur, decay, tones, seq):
    t = i / _RATE
    if seq:
        half = dur / 2.0
        k = 0 if t < half else min(1, len(tones) - 1)
        f, a = tones[k]
        lt = t if k == 0 else t - half
        env = math.exp(-lt * (decay if k == 0 else decay * 1.25))
        amp = a * math.sin(2 * math.pi * f * lt)
    else:
        env = math.exp(-t * decay)
        amp = sum(a * math.sin(2 * math.pi * f * t) for f, a in tones)
    fade_in = min(1.0, t / 0.005)                     # 5ms 淡入，压住爆点
    fade_out = min(1.0, max(0.0, (dur - t) / 0.06))   # 60ms 淡出，免截断咔哒
    return amp * env * fade_in * fade_out


def build(template="ding", volume=0.7):
    """生成 wav 到临时目录，返回路径。同参数复用已生成的文件。"""
    if template not in TEMPLATES or TEMPLATES[template][1] is None:
        template = "ding"
    spec = TEMPLATES[template][1]
    vol = max(0.05, min(0.95, float(volume)))
    path = os.path.join(_TMP, f"dafeiyu_snd_{template}_{int(round(vol * 100))}.wav")
    if os.path.exists(path):
        return path

    n = int(_RATE * spec["dur"])
    # 衰减包络会把实际峰值压到理论振幅的 1/3 左右，这里补回来：
    # 默认 0.7 音量落在 -9dBFS 上下——听得清，又不至于被手机麦录得一清二楚。
    peak = 1.15 * vol * (1.35 if spec["seq"] else 1.0)
    peak = min(0.99, peak)
    frames = bytearray()
    for i in range(n):
        v = max(-1.0, min(1.0, _sample(i, spec["dur"], spec["decay"],
                                      spec["tones"], spec["seq"])))
        frames += struct.pack("<h", int(v * 32767 * peak))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(bytes(frames))
    return path


def _src_frames(template, volume):
    """拿一段音频：(缓存用的标签, 源文件, wave 参数, 帧数据)。

    标签必须反映**实际用的**音源：自定义文件丢了会退回 ding，
    这时候缓存名还叫 custom_xx_x2.wav，下次真选了自定义就会撞名。
    """
    if template == "custom":
        cfg = load_cfg()
        p = str(cfg.get("custom_path") or "")
        if p and os.path.isfile(p):
            with wave.open(p, "rb") as w:
                return "custom", p, w.getparams(), w.readframes(w.getnframes())
        template = "ding"
    if template not in TEMPLATES or TEMPLATES[template][1] is None:
        template = "ding"
    src = build(template, volume)
    with wave.open(src, "rb") as w:
        return template, src, w.getparams(), w.readframes(w.getnframes())


def build_times(n, template="ding", volume=0.7, gap=0.16):
    """把 n 声拼成一个 wav。

    为什么不能循环调 play()：`winsound.PlaySound` 同一时刻只播一个声音，
    第二次调用会把第一个掐掉。所以「叮两声」必须合成成一个文件再播一次，
    否则听起来永远只有最后一声。
    """
    n = max(1, min(6, int(n)))
    vol = max(0.05, min(0.95, float(volume)))
    if n == 1:
        return _src_frames(template, vol)[1]

    tag, src, params, frames = _src_frames(template, vol)
    path = os.path.join(_TMP, f"dafeiyu_snd_{tag}_{int(round(vol * 100))}_x{n}.wav")
    if os.path.exists(path):
        return path

    one = params.sampwidth * params.nchannels
    silence = b"\x00" * (int(params.framerate * gap) * one)
    data = (frames + silence) * (n - 1) + frames
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(params.sampwidth)
            w.setframerate(params.framerate)
            w.writeframes(data)
    except Exception as e:
        print("[ding] 合成连音失败，退回单声:", e)
        return src
    return path


def play_times(n=1, template=None, volume=None, block=False):
    """连播 n 声，语义同 play()。n<=0 直接不响。

    block=True 用同步播放，进程会等到声音放完——给「调用方马上就退出」的场景用
    （hook 脚本），否则 ASYNC 的声会跟着进程一起被掐掉。
    """
    n = int(n)
    if n <= 0:
        return False
    try:
        import winsound
    except ImportError:
        return False
    try:
        cfg = load_cfg()
        if template is None:
            if not cfg.get("enabled", True):
                return False
            template = cfg.get("template", "ding")
        if volume is None:
            volume = cfg.get("volume", 0.7)
        flags = winsound.SND_FILENAME | winsound.SND_NODEFAULT
        if not block:
            flags |= winsound.SND_ASYNC
        winsound.PlaySound(build_times(n, template, volume), flags)
        return True
    except Exception as e:
        print("[ding] 连音播放失败:", e)
        return play(template, volume)


def play(template=None, volume=None):
    """播一声。不传参数就按 sound_config.json 来。

    返回 True 表示已交给系统（不代表一定听得见：可能静音了）。
    两个参数都显式传 = 试听，不写配置也不看开关。
    """
    try:
        import winsound
    except ImportError:                 # 非 Windows，静默跳过
        return False

    try:
        cfg = load_cfg()
        if template is None:
            if not cfg.get("enabled", True):
                return False
            template = cfg.get("template", "ding")
        if volume is None:
            volume = cfg.get("volume", 0.7)

        if template == "custom":
            p = str(cfg.get("custom_path") or "")
            if p and os.path.isfile(p):
                winsound.PlaySound(
                    p, winsound.SND_FILENAME | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT)
                return True
            template = "ding"          # 自定义文件丢了，退回默认音

        winsound.PlaySound(
            build(template, volume),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception as e:
        print("[ding] 播放失败:", e)
        try:
            winsound.MessageBeep()      # 兜底：系统默认一声
            return True
        except Exception:
            return False


if __name__ == "__main__":
    import sys
    import time

    if not os.path.exists(CFG_PATH):
        save_cfg(DEFAULT_CFG)
        print("写出默认配置 ->", CFG_PATH)
    c = load_cfg(force=True)
    print("当前配置:", c)

    if "--all" in sys.argv:
        for k, (label, spec) in TEMPLATES.items():
            if spec is None:
                continue
            p = build(k, c.get("volume", 0.7))
            print(f"  {label:14s} {os.path.getsize(p):>7d} 字节  {os.path.basename(p)}")
            play(k, c.get("volume", 0.7))
            time.sleep(spec["dur"] + 0.35)
    else:
        print("播放:", play())
        time.sleep(0.8)

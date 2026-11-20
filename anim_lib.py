# -*- coding: utf-8 -*-
"""素材动画库：把烤好的序列帧按需读进来，给桌宠当动作播。

设计要点（第二版，为 109 个动作重写）：

- **环形滑动窗口，不再整段驻留**。
  第一版是把一个动作的全部帧一次性解码成 QPixmap 存起来。22 个动作、
  37 帧时是「4 个动作 ≈ 58 MB」，还能忍；109 个动作、100 帧就变成
  「4 个动作 ≈ 156 MB」，进程直接 200 MB。

  但桌宠播动作是**严格顺序**的（按已播秒数取帧，从不随机跳），
  所以只需要保留「刚播过的几帧 + 即将播的二十几帧」。
  内存恒定 ≈ 30 帧 × 0.4 MB ≈ 12 MB，**和动作数量、时长相无关**。

- **读进来就是渲染尺寸**：烘焙时已经按窗口高度出图，这里的缩放只是兜底。
- 素材缺失时全部返回 None，调用方退回静态图，不会崩。

帧文件名约定：<目录>/f000.webp、f001.webp ……（也兼容 .png）
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap


class _Clip:
    """一个动作的帧窗口。只装「当前在播的这一段」，播过去就丢。"""

    __slots__ = ("paths", "n", "h", "ahead", "behind", "win")

    def __init__(self, paths, height, ahead, behind):
        self.paths = paths
        self.n = len(paths)
        self.h = int(height or 0)
        self.ahead = max(1, int(ahead))
        self.behind = max(0, int(behind))
        self.win = {}                      # idx -> QPixmap

    def _load(self, i):
        pm = QPixmap(self.paths[i])
        if pm.isNull():
            return None
        if self.h and pm.height() != self.h:
            pm = pm.scaledToHeight(self.h, Qt.TransformationMode.SmoothTransformation)
        return pm

    def _trim(self, cur):
        """把窗口收到 [cur-behind, cur+ahead]，其余立刻释放。"""
        span = self.behind + self.ahead + 1
        if self.n <= span:                 # 短动作，全留着更省事
            return
        keep = {(cur + k) % self.n for k in range(-self.behind, self.ahead + 1)}
        for k in [k for k in self.win if k not in keep]:
            del self.win[k]

    def frame(self, idx):
        if not self.n:
            return None
        i = idx % self.n
        pm = self.win.get(i)
        if pm is None:
            pm = self._load(i)
            if pm is None:
                return None
            self.win[i] = pm
            self._trim(i)
        return pm


class AnimLib:
    def __init__(self, root, height, ahead=26, behind=6, max_clips=4):
        self.root = root
        self.height = int(height)
        self.ahead = ahead
        self.behind = behind
        self.max_clips = max_clips
        self.meta = {}
        self.canvas = (0, 0)
        self._clips = {}                   # name -> _Clip（只存句柄，帧在窗口里）
        self._order = []                   # LRU 顺序
        try:
            with open(os.path.join(root, "meta.json"), encoding="utf-8") as f:
                self.meta = json.load(f)
        except Exception:
            self.meta = {}
        self.canvas = tuple(self.meta.get("canvas") or (0, 0))
        self.anims = self.meta.get("anims") or {}

    # ---------- 查询 ----------
    def has(self, name):
        return name in self.anims and os.path.isdir(os.path.join(self.root, name))

    def names(self):
        return sorted(self.anims)

    def seconds(self, name):
        d = self.anims.get(name) or {}
        fps = d.get("fps") or self.meta.get("fps") or 15
        return (d.get("frames") or 1) / max(1, fps)

    def fps(self, name):
        return (self.anims.get(name) or {}).get("fps") or self.meta.get("fps") or 15

    # ---------- 句柄 ----------
    def _clip(self, name):
        """取（必要时建）一个动作的句柄。不读图，只登记文件列表。"""
        c = self._clips.get(name)
        if c is not None:
            if name in self._order:
                self._order.remove(name)
            self._order.append(name)
            return c
        d = os.path.join(self.root, name)
        try:
            files = sorted(f for f in os.listdir(d)
                           if f.endswith(".webp") or f.endswith(".png"))
        except OSError:
            return None
        if not files:
            return None
        c = _Clip([os.path.join(d, f) for f in files], self.height,
                  self.ahead, self.behind)
        self._clips[name] = c
        self._order.append(name)
        # 只留最近用过的几个句柄，别的整段释放
        while len(self._order) > self.max_clips:
            old = self._order.pop(0)
            self._clips.pop(old, None)
        return c

    # ---------- 取帧 ----------
    def frame_at(self, name, t):
        """按已播放秒数取帧（自动循环）。t 用秒，比传帧号好算。"""
        c = self._clip(name)
        if c is None:
            return None
        return c.frame(int(t * self.fps(name)))

    def frame_between(self, name, t, f0=0.0, f1=1.0, rate=1.0):
        """只在 [f0, f1] 这段帧区间里循环取帧（比例 0~1），t 为秒。

        给「移动动画只播走路那一段」用：素材里常有一截起步前的静帧和
        收尾的定住帧，整圈循环会让腿看着停了半秒，脚就跟着打滑。
        区间外的时间按区间长度取模，动画始终处于「在走」的状态。
        """
        c = self._clip(name)
        if c is None:
            return None
        n = max(1, c.n)
        a = max(0, min(n - 1, int(round(f0 * n))))
        b = max(a + 1, min(n, int(round(f1 * n))))
        span = b - a
        idx = a + int(t * self.fps(name) * max(0.1, float(rate))) % span
        return c.frame(idx)

    def first(self, name):
        c = self._clip(name)
        return c.frame(0) if c else None

    def warm(self, names):
        """预热：只装第一帧，别整段读——那是第一版爆炸的原因。"""
        for n in names:
            self.first(n)

    def drop(self, name):
        self._clips.pop(name, None)
        if name in self._order:
            self._order.remove(name)

    def reset_height(self, height):
        """窗口改了大小：所有句柄换高度，已解码的帧全丢。

        帧是「读进来就按渲染尺寸」的，旧高度的图留着就是一张小图贴在
        大窗口里——改尺寸没反应、鱼看着没变，就是这么来的。
        """
        h = int(height or 0)
        if h == self.height:
            return False
        self.height = h
        for c in self._clips.values():
            c.h = h
            c.win.clear()
        return True

    def stats(self):
        """调试用：当前真实驻留了多少帧。"""
        return {n: len(c.win) for n, c in self._clips.items()}

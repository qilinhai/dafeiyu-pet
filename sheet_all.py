# -*- coding: utf-8 -*-
"""把烤好的动作各取一帧拼成总览图，用来肉眼核对：

1. 有没有被裁到（角色贴边、缺手缺脚）
2. 有没有全透明（动作烤失败了但没报错）
3. 不同动作之间角色大小、位置是否一致

109 个动作，10 列排开，每格标名字。
"""
from __future__ import annotations

import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets_anim_v2")
OUT = os.path.join(HERE, "overview.png")

COLS = 10
CELL = 150
LABEL = 16


def _font(size=11):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def main():
    with open(os.path.join(SRC, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    anims = meta["anims"]
    names = sorted(anims)
    f = _font()
    rows = (len(names) + COLS - 1) // COLS
    W, H = COLS * CELL, rows * (CELL + LABEL)
    sheet = Image.new("RGB", (W, H), (238, 236, 232))
    d = ImageDraw.Draw(sheet)

    for i, name in enumerate(names):
        d_ = os.path.join(SRC, name)
        files = sorted(x for x in os.listdir(d_) if x.startswith("f"))
        if not files:
            continue
        mid = Image.open(os.path.join(d_, files[len(files) // 2])).convert("RGBA")
        # 等比缩到格子内
        k = min((CELL - 6) / mid.width, (CELL - 6) / mid.height)
        im = mid.resize((max(1, round(mid.width * k)), max(1, round(mid.height * k))),
                        Image.LANCZOS)
        cx = (i % COLS) * CELL
        cy = (i // COLS) * (CELL + LABEL)
        # 棋盘格底，透明区域一眼可见
        for yy in range(0, CELL, 12):
            for xx in range(0, CELL, 12):
                c = (225, 222, 216) if (xx // 12 + yy // 12) % 2 else (245, 243, 238)
                d.rectangle([cx + xx, cy + yy, cx + xx + 11, cy + yy + 11], fill=c)
        sheet.paste(im, (cx + (CELL - im.width) // 2,
                         cy + (CELL - im.height) // 2), im)
        tag = name if len(name) <= 16 else name[:15] + "…"
        d.text((cx + 3, cy + CELL + 2), tag, fill=(40, 40, 48), font=f)

    sheet.save(OUT)
    print(f"总览 {len(names)} 个动作 → {OUT}  {W}×{H}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

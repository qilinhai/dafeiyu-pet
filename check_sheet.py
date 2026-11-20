# -*- coding: utf-8 -*-
"""把所有动作的中间帧拼成一张总览图，用来目视检查。

为什么要这个：109 个动作各烤一遍，靠一帧帧点开看是不可能的。
拼成一张表，一眼就能发现「哪个动作被裁掉了」「哪个角色大小不对」。

用法：
    python check_sheet.py                 # 全部，输出 _sheet.png
    python check_sheet.py --cols 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ANIM = os.path.join(HERE, "assets_anim_v2")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=10)
    ap.add_argument("--cell", type=int, default=120)
    ap.add_argument("--out", default=os.path.join(HERE, "_sheet.png"))
    args = ap.parse_args()

    meta = json.load(open(os.path.join(ANIM, "meta.json"), encoding="utf-8"))
    anims = meta.get("anims") or {}
    names = sorted(anims)
    print(f"{len(names)} 个动作")

    cols = args.cols
    rows = (len(names) + cols - 1) // cols
    cell = args.cell
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 14)), (32, 32, 36))

    from PIL import ImageDraw
    dr = ImageDraw.Draw(sheet)
    edge_cut = []
    for i, n in enumerate(names):
        d = os.path.join(ANIM, n)
        files = sorted(f for f in os.listdir(d) if f.startswith("f"))
        if not files:
            continue
        mid = files[len(files) // 2]

        # 边缘检查：四边有不透明像素 = 被裁了
        im = Image.open(os.path.join(d, mid)).convert("RGBA")
        a = im.split()[-1]
        w, h = im.size
        px = a.load()
        cut = []
        for x in range(w):
            if px[x, 0] > 128 or px[x, h - 1] > 128:
                cut.append("上下")
                break
        for y in range(h):
            if px[0, y] > 128 or px[w - 1, y] > 128:
                cut.append("左右")
                break
        if cut:
            edge_cut.append((n, "/".join(sorted(set(cut)))))

        thumb = im.copy()
        thumb.thumbnail((cell - 6, cell - 6), Image.LANCZOS)
        cx = (i % cols) * cell
        cy = (i // cols) * (cell + 14)
        bg = Image.new("RGB", (cell, cell), (24, 24, 28))
        bg.paste(thumb, ((cell - thumb.width) // 2,
                         (cell - thumb.height) // 2), thumb)
        sheet.paste(bg, (cx, cy))
        dr.text((cx + 4, cy + cell + 1), n[:16], fill=(190, 190, 200))

    sheet.save(args.out)
    print(f"已存 {args.out}  ({sheet.width}×{sheet.height})")
    if edge_cut:
        print(f"\n⚠ 有 {len(edge_cut)} 个动作贴到画布边缘（可能被裁）：")
        for n, side in edge_cut:
            print(f"   {n:18} {side}")
    else:
        print("✓ 没有动作贴边，裁切框够用")
    return 0


if __name__ == "__main__":
    sys.exit(main())

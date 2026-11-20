# -*- coding: utf-8 -*-
"""换挡自检：窗口 / 静态图 / 素材帧三者必须同一个高度。

起因：菜单里点「大」，cfg 改了、窗口也大了，但画面上的鱼没变——
animlib 是按渲染高度烤帧的，set_size 没告诉它，一直画 187 的旧帧。
"""
import os, sys
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); BASE = APP
sys.path.insert(0, BASE); sys.path.insert(0, APP); os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
import pet

# 谁都不许把 offscreen 窗口的位置/尺寸写进真配置（踩过一次：测试把
# 用户桌宠挪到了 468,419 还改了大小）
pet.PetWindow.save_config = lambda self: None

FAIL = []
def ok(cond, name, info=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{info}]" if info else ""))
    if not cond:
        FAIL.append(name)

def main():
    w = pet.PetWindow()
    print(f"素材动画: {'有' if w.animlib else '无（退回静态图）'}")
    orig = w.cfg["size"]
    for label, mult in pet.SIZE_LEVELS.items():
        w.set_size(mult)
        h = w.cur_h
        ok(abs(w.cfg["size"] - mult) < 1e-6, f"{label}：配置写进去了", f"size={w.cfg['size']}")
        ok(h == round(340 * mult), f"{label}：cur_h 正确", str(h))
        ok(w.width() == w.win_w and w.height() == h + pet.BUBBLE_H + pet.MARGIN * 2 + 10,
           f"{label}：窗口尺寸跟上了", f"{w.width()}x{w.height()}")
        import os as _os
        pre = _os.path.join(pet.SPRITE_DIR, f"正面_{h}.png")
        ok(_os.path.exists(pre), f"{label}：用的是预烤图，不现场缩放",
           "正面_%d.png" % h)
        if w.animlib is not None:
            ok(w.animlib.height == h, f"{label}：动画库高度同步", f"animlib={w.animlib.height} cur_h={h}")
        # 真取一帧，看它画出来多高
        w.clip = "idle" if (w.animlib and w.animlib.has("idle")) else None
        w.clip_t0 = 0.0
        fr = w.clip_frame()
        if fr is not None:
            ok(abs(fr.height() - h) <= 2, f"{label}：素材帧就是新高度", f"frame={fr.height()} 期望={h}")
        else:
            ok(w.animlib is None, f"{label}：没有素材时走静态图（不报错）")
        # 静态图键必须存在，否则 draw_one 会 KeyError
        for direction, name in (("down", "正面"), ("left", "侧面"), ("up", "背面")):
            w.dir = direction
            k = w._sprite_key()
            # sprites 的键是 (名字, 高度)，朝向单独传给 draw_pix，别照着三元组查
            ok(k[0] == name and (k[0], k[1]) in w.sprites,
               f"{label}：{name} 静图在库里", str(k))
    w.set_size(orig)
    w.close()
    return 0 if not FAIL else 1

def run():
    code = main()
    QTimer.singleShot(80, lambda: app.exit(code))

QTimer.singleShot(120, run)
code = app.exec()
print("\n" + "=" * 56)
print(f"{'全部通过' if not FAIL else '失败 ' + str(len(FAIL)) + ' 项: ' + ', '.join(FAIL)}")
sys.exit(code if FAIL else 0)

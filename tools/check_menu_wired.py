# -*- coding: utf-8 -*-
"""菜单全项「线通不通」检查。

不触发动作（放了会弹窗、连手机、退出），只看每个菜单项的回调能不能落地：
- 回调是 self 的方法：方法必须存在
- 回调是 lambda：里面出现的 self.xxx 必须存在
- 回调为空：该项点了不会有任何事（除分隔线）

抓的就是「菜单里有这一项，但点了没反应」这类问题。
"""
import os
import sys
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
import pet

FAIL = []


def attrs_in(cb):
    """扒出所有「紧跟在 self 后面」的属性名。

    只看 co_names 会误报：`dt.CFG_PATH` 的 CFG_PATH 也在里面。所以过一遍
    字节码，只有上一条指令是 LOAD_FAST self 的 LOAD_ATTR 才算 self 的成员。
    """
    import dis
    out = set()

    def scan(code):
        prev = None
        for ins in dis.get_instructions(code):
            if ins.opname == "LOAD_ATTR" and prev is not None \
                    and prev.opname == "LOAD_FAST" and prev.argval == "self":
                out.add(ins.argval)
            prev = ins
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                scan(const)

    c = getattr(cb, "__code__", None)
    if c is not None:
        scan(c)
    return out


def check_cb(w, text, cb, where, item=None):
    # 回调有两条路：构造时传 _cb，或者 it.triggered.connect(...)
    wired = bool(item is not None and item.receivers("2triggered(bool)") > 0)
    if cb is None:
        if wired:
            return "signal"
        FAIL.append(f"{where} → 「{text}」没有回调")
        return "空"
    if isinstance(cb, types.MethodType):
        return "方法"
    if callable(cb):
        names = attrs_in(cb)
        missing = [n for n in names
                   if not n.startswith("__")
                   and hasattr(w, n) is False
                   and n not in ("checked",)
                   and _looks_like_member(n)]
        if missing:
            FAIL.append(f"{where} → 「{text}」引用了不存在的东西: {missing}")
            return "断线"
        return "lambda"
    return "非函数"


def _looks_like_member(name):
    """co_names 里混着全局函数名、内置名，只挑「可能是 self 成员」的。

    判据：PetWindow 类里有同名方法/属性吗？没有才怀疑；但全局函数
    （random.choice 之类）也在 co_names，所以先排除模块级同名。
    """
    if hasattr(pet, name):
        return False
    if name in dir(__builtins__) if isinstance(__builtins__, dict) else False:
        return False
    import builtins
    if hasattr(builtins, name):
        return False
    return True


def walk(w, menu, depth=0, where="根"):
    n = 0
    for it in menu._items:
        if it.sep:
            continue
        n += 1
        if not it.isEnabled():
            continue                     # 灰色提示行（状态、计数）本来就不该点
        path = f"{where}/{it.text()[:20]}"
        if it.sub is not None:
            n += walk(w, it.sub, depth + 1, path)
        else:
            check_cb(w, it.text(), getattr(it, "_cb", None), path, it)
    return n


def main():
    w = pet.PetWindow()
    m = w._build_menu()
    total = walk(w, m)
    print(f"菜单项 {total} 个")
    if FAIL:
        print(f"\n⚠ {len(FAIL)} 处需要看：")
        for f in FAIL:
            print("   -", f)
        return 1
    print("每一项都有可落地的回调")
    return 0


def run():
    code = main()
    QTimer.singleShot(60, lambda: app.exit(code))


QTimer.singleShot(120, run)
code = app.exec()
sys.exit(code)

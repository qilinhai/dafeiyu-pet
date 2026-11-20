# -*- coding: utf-8 -*-
"""发布树的导入冒烟：每个模块单独在一个子进程里 import，避免互相污染。

用法：python tools/smoke_import.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)

MODULES = [
    "anim_config", "anim_lib", "anim_manifest", "awareness", "chime", "cleaner",
    "credits_guard", "ding", "douyin_archive", "douyin_panel", "douyin_take",
    "file_feeding", "memory_panel", "move_stride", "board_link", "bridge_server",
    "notify_voice", "pet_acts", "pet_growth", "pet_hunch", "pet_memory",
    "phone_connect_dialog", "phone_link", "phone_notify", "phone_panel",
    "say_ai", "settings_panel", "tts_breeze", "voice_lexicon", "voice_style",
    "wb_menu", "zhengzhou_today", "pet",
]

bad = []
for m in MODULES:
    r = subprocess.run([sys.executable, "-c", f"import {m}"], cwd=APP,
                       capture_output=True, text=True, timeout=90)
    ok = r.returncode == 0
    tail = (r.stderr or "").strip().splitlines()
    print(("OK   " if ok else "FAIL ") + m + ("" if ok else "  <- " + (tail[-1] if tail else "")))
    if not ok:
        bad.append(m)

print()
print(f"{len(MODULES) - len(bad)}/{len(MODULES)} 通过")
sys.exit(1 if bad else 0)

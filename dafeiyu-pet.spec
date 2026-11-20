# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

注意：打包后 sys._MEIPASS 是 <exe 所在目录>/_internal，
      以 __file__ 定位的数据文件（persona.json / pet_memory.json 等）会落在那里。
      onedir 不会被删，所以记忆和人设能跨次保留；config.json 仍写在 exe 旁边。

用法：
    pyinstaller --noconfirm dafeiyu-pet.spec
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("assets_anim_v2", "assets_anim_v2"),
    ("sprites", "sprites"),
    ("icon.ico", "."),
    # 首次运行要能读到的默认配置
    ("persona.json", "."),
    ("ai_talk.json", "."),
    ("anim_config.json", "."),
    ("cleaner_config.json", "."),
    ("douyin_config.json", "."),
    ("move_strides.json", "."),
    ("notify_config.json", "."),
    ("sound_config.json", "."),
    ("voice_lexicon.json", "."),
]

binaries = []
hiddenimports = [
    # 运行时按需 import 的本地模块，静态分析抓不到
    "anim_config", "anim_lib", "anim_manifest", "awareness", "board_link",
    "bridge_server", "chime", "cleaner", "credits_guard", "ding",
    "douyin_archive", "douyin_panel", "douyin_take", "file_feeding",
    "memory_panel", "move_stride", "notify_voice", "pet_acts", "pet_growth",
    "pet_hunch", "pet_memory", "phone_connect_dialog", "phone_link",
    "phone_notify", "phone_panel", "say_ai", "settings_panel", "tts_breeze",
    "voice_lexicon", "voice_style", "wb_menu", "zhengzhou_today",
    # Qt 里靠插件加载的部分
    "PySide6.QtMultimedia", "PySide6.QtNetwork", "PySide6.QtSvg",
]

# PySide6 不能 collect_all：会把 WebEngine / 3D / Charts 全拖进来（实测多出 650MB）。
# PyInstaller 自带的 PySide6 hook 只会收真正 import 到的那几个模块。
for pkg in ("psutil", "requests", "pynvml", "certifi", "urllib3", "idna", "charset_normalizer"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["pet.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "av", "numpy", "PIL", "pytest", "setuptools",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtDesigner",
        "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
        "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtPdf", "PySide6.QtHelp",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DaFeiYuPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=["icon.ico"],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DaFeiYuPet",
)

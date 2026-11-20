"""Windows 本地文件投喂。文件和文件夹都能吃，快捷方式只回收快捷方式本身。

只碰「路径」和「名字」，从不打开文件。播报和给模型的判断都基于
文件名 + 扩展名——读内容既没必要，也白花 token。
"""
import ctypes
import os
import random
from ctypes import wintypes

from PySide6.QtCore import QFile

# 按扩展名归类，只为了播报时说得出「2 个图片、1 个文档」。
# 不认识的算「其他文件」——宁可口径粗，也不能把未知类型硬塞进某个类里。
_KINDS = (
    (("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "svg", "tif", "tiff"), "图片"),
    (("mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v"), "视频"),
    (("mp3", "wav", "flac", "m4a", "aac", "ogg", "wma"), "音频"),
    (("doc", "docx", "wps", "pdf", "txt", "md", "rtf", "pages"), "文档"),
    (("xls", "xlsx", "csv", "et", "numbers"), "表格"),
    (("ppt", "pptx", "dps", "key"), "演示"),
    (("zip", "rar", "7z", "tar", "gz", "xz"), "压缩包"),
    (("exe", "msi", "apk", "dmg", "pkg"), "安装包"),
)
_ORDER = ["文件夹"] + [label for _, label in _KINDS] + ["其他文件"]


def is_folder(path):
    """算不算「整个目录」。符号链接/junction 算文件——只回收链接本身，别顺着删。"""
    return os.path.isdir(path) and not os.path.islink(path)


def drop_paths(urls):
    """拖进来的东西，能吃的才算。

    文件夹也算——老版本只认 os.path.isfile，拖文件夹进来整批被拒，
    所以「吃不了文件夹」。网络路径和已经不存在的仍然不收。
    有一个吃不动就整批不吃：半吃半留比不吃更让人懵。
    """
    paths = []
    for url in urls:
        if not url.isLocalFile():
            return []
        path = os.path.normpath(url.toLocalFile())
        if not os.path.isabs(path) or path.startswith(("\\\\", "//")):
            return []
        if not os.path.exists(path):
            return []
        paths.append(path)
    return list(dict.fromkeys(paths))


def display_name(path):
    """路径转成能念、能显示的名字。盘符根目录（D:\\）就取 D:\\。"""
    text = str(path or "").rstrip("\\/")
    return os.path.basename(text) or text or str(path or "")


def summarize(paths):
    """归类成一句人话：'2 个图片、1 个文件夹'。算不出来就返回空串。"""
    counts = {}
    for path in paths or []:
        if is_folder(path):
            counts["文件夹"] = counts.get("文件夹", 0) + 1
            continue
        ext = os.path.splitext(str(path))[1].lower().lstrip(".")
        for exts, label in _KINDS:
            if ext in exts:
                counts[label] = counts.get(label, 0) + 1
                break
        else:
            counts["其他文件"] = counts.get("其他文件", 0) + 1
    return "、".join(f"{counts[label]} 个{label}" for label in _ORDER if label in counts)


def names_hint(paths, limit=4, each=18):
    """给模型看的名字清单。只有名字，没有内容——它靠这个判断清了什么东西。

    只给前几个：一屏名字既贵又没必要，总数用「等 N 项」带过。
    """
    paths = list(paths or [])
    names = []
    for path in paths[:limit]:
        name = display_name(path)
        if len(name) > each:
            name = name[:each] + "…"
        names.append(name + ("/" if is_folder(path) else ""))
    text = "、".join(names)
    if len(paths) > len(names):
        text += f" 等 {len(paths)} 项"
    return text


# 吃完那句话的几个说法。事实都一样，说法换着来——模型不吭声的时候
# 也不至于变成复读机，同一句听十遍就是噪音了。
_OK_SHAPES = (
    "吃下 {w}，都放进回收站了。",
    "{w}，一个不剩，全进回收站了。",
    "这一口吃了 {w}，桌面能喘口气了。",
    "{w}，清完了。",
)


def fed_line(count, failed, summary="", names=""):
    """吃完之后说什么。返回 (气泡里写的, 念出来的)。

    气泡带类型摘要，念的时候不带括号——括号里的顿号送进 TTS 会拖拍子。
    只吃了一个文件夹时改说「文件夹「X」」，不然「1 个文件（1 个文件夹）」很别扭。
    """
    count = int(count or 0)
    bad = len(failed or [])
    if count <= 0:
        line = f"{bad} 个一个都没吃成，全还在原地。" if bad else "什么也没吃成。"
        return line, line

    parts = [p for p in (summary or "").split("、") if p]
    if parts and all(p.endswith("个文件夹") for p in parts):
        # 全是文件夹：别套成「1 个文件（1 个文件夹）」，直接说文件夹。
        # 只吃一个时把名字带上——不然「1 个文件夹」他根本不知道是哪个。
        bubble_w = summary + (f"「{names.rstrip('/')}」" if count == 1 and names else "")
        spoken_w = summary
    else:
        # 混着文件夹的时候不说「N 个文件」，文件夹不算文件，说「项」
        unit = "项" if any(p.endswith("个文件夹") for p in parts) else "个文件"
        bubble_w = f"{count} {unit}（{summary}）" if summary else f"{count} 个文件"
        spoken_w = summary or f"{count} 个文件"

    if bad:
        return (f"{bubble_w}，{count} 个进了回收站，{bad} 个没吃动，还在原地。",
                f"{spoken_w}里，{count} 个进了回收站，{bad} 个没吃动，还在原地。")

    shape = random.choice(_OK_SHAPES)
    return shape.format(w=bubble_w), shape.format(w=spoken_w)


# ---------------------------------------------------------------- 送回收站

_FO_DELETE = 3
_FOF_ALLOWUNDO = 0x40
_FOF_NOCONFIRMATION = 0x10
_FOF_SILENT = 0x04
_FOF_NOERRORUI = 0x400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def _shell_trash(path):
    """换个更宽的接口再试一次：shell 的回收站。

    QFile 走的是 Qt 自己的封装，被占用的目录、超长路径、某些同步盘目录
    会直接返回 false；shell 这条路能过。代价是要先给当前线程配 COM
    （投喂线程是普通 Python 线程，没初始化过）。
    """
    if os.name != "nt":
        return False
    try:
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # STA，SHFileOperation 要求
        try:
            op = _SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = _FO_DELETE
            # pFrom 要求「字符串 + 两个 \0」：c_wchar_p 自己补一个，这里再补一个
            op.pFrom = ctypes.c_wchar_p(str(path) + "\0")
            op.pTo = None
            op.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION
                         | _FOF_SILENT | _FOF_NOERRORUI)
            code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        finally:
            if hr in (0, 1):          # S_OK / S_FALSE（本来就初始化过，才轮到我们收）
                ctypes.windll.ole32.CoUninitialize()
        return code == 0 and not os.path.exists(path)
    except (OSError, AttributeError, ValueError):
        return False


def _trash_folder(path):
    return QFile(path).moveToTrash() or _shell_trash(path)


def recycle_files(paths):
    """一条一条送进回收站。文件和文件夹都能吃。

    返回 (成功条数, 没吃动的名字)。文件夹单列一条路：QFile 删目录在
    不少机器上会失败，失败后再走 shell，两条都不成才算没吃动。
    """
    count = 0
    failed = []
    for path in paths:
        try:
            done = _trash_folder(path) if is_folder(path) else QFile(path).moveToTrash()
        except OSError:
            done = False
        if done:
            count += 1
        else:
            failed.append(display_name(path))
    return count, failed

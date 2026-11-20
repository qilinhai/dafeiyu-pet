"""释放资源 —— 按「应用」而不是「进程名」来管。

为什么按应用：同一个微信 = Weixin.exe×5 + WeChatAppEx.exe×9 + crashpad_handler.exe，
落在两个目录、三个名字。按名字分组会把它劈成三份，各判各的：
你想清掉微信，得加三个名字，漏一个只清一半；更糟的是把微信的崩溃上报进程
当成独立程序单独杀掉。

四层：
  1. 探测   枚举进程（名字/路径/父进程/内存/可见窗口），只读
  2. 归组   按「产品目录」合并；再沿父进程链把同应用的不同目录收拢
            （实测 WeChatAppEx 的祖先进程就是 Weixin.exe，能自动认出来）
  3. 裁决   每个应用四态：保护 / 只关窗 / 可清理 / 不管（默认不管）
  4. 执行   只动「只关窗」和「可清理」两组，杀前复查窗口，事后复查是否自己回来

安全底线（代码写死，配置改不掉）：
  · NEVER 里的进程名所在的应用一律锁成「保护」，面板上改不动
  · 默认全部「不管」：没被用户亲手改成 clean 的应用，永远不会被结束
"""

import ctypes
import json
import os
import re
import time
from collections import defaultdict
from ctypes import wintypes as wt
from pathlib import Path

try:
    import psutil
except ImportError:                                   # 缺了也不能让菜单打不开
    psutil = None

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "cleaner_config.json"

# ---------------------------------------------------------------- 四态
MODE_PROTECT = "protect"
MODE_CLOSE = "close"
MODE_CLEAN = "clean"
MODE_IGNORE = "ignore"
MODES = (MODE_PROTECT, MODE_CLOSE, MODE_CLEAN, MODE_IGNORE)
MODE_TEXT = {
    MODE_PROTECT: "保护",
    MODE_CLOSE: "只关窗",
    MODE_CLEAN: "可清理",
    MODE_IGNORE: "不管",
}

# ---------------------------------------------------------------- 硬保护
# 系统内脏 / 杀软 / 输入法 / 桌宠自己 / WorkBuddy。代码里写死，配置改不掉。
# 命中这里的进程名，它所在的应用整组锁成「保护」。
NEVER = {
    # Windows 自身
    "system", "registry", "smss.exe", "smss", "csrss.exe", "csrss",
    "wininit.exe", "wininit", "winlogon.exe", "winlogon", "services.exe",
    "services", "lsass.exe", "lsass", "svchost.exe", "svchost",
    "dwm.exe", "dwm", "fontdrvhost.exe", "fontdrvhost",
    "conhost.exe", "conhost", "sihost.exe", "sihost",
    "taskhostw.exe", "taskhostw", "explorer.exe", "explorer",
    "ctfmon.exe", "ctfmon", "audiodg.exe", "audiodg",
    "spoolsv.exe", "spoolsv", "wmiprvse.exe", "wmiprvse",
    "searchhost.exe", "searchhost", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "applicationframehost.exe",
    "runtimebroker.exe", "textinputhost.exe", "lockapp.exe",
    "dllhost.exe", "shellexperiencehost", "inputswitchtoasttip.exe",
    # 安全软件
    "hipsdaemon.exe", "hipstray.exe", "hipsmain.exe", "usysdiag.exe",
    "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe",
    "360tray.exe", "360sd.exe", "zhudongfangyu.exe", "qqpctray.exe",
    # 输入法
    "imeservice.exe", "chsime.exe", "sogoucloud.exe", "sgtool.exe",
    "qqpy.exe", "qqpinyin.exe", "wetype.exe",
    # 桌宠自己 + 运行时
    "python.exe", "pythonw.exe", "python", "pythonw", "launcher.exe",
    # WorkBuddy / 开发工具（杀了这个对话就没了）
    "workbuddy.exe", "workbuddy", "codebuddy.exe", "node.exe", "node",
    "msedgewebview2.exe", "adb.exe",
}

# 产品目录往上走时跳过的「非产品名」层
GENERIC_DIRS = {
    "bin", "bin64", "bin32", "app", "apps", "application", "current", "latest",
    "x64", "x86", "x86_64", "amd64", "64", "32", "release", "debug", "resources",
    "contents", "framework", "frameworks", "lib", "libs", "core", "plugins",
    "versions", "version", "update", "updater", "modules", "runtime", "helpers",
    "helper", "subprocess", "app.asar.unpacked", "vendor",
    # 跨平台产物目录：node_modules/@x/y/bin/win32-x64/ 这种最后一级不是产品名
    "win32-x64", "win32-ia32", "win32-arm64", "darwin-x64", "darwin-arm64",
    "linux-x64", "linux-arm64", "win-unpacked", "mac", "macos", "obj", "out",
    # 产品内部的运行层：真正的产品名在上一层（NVIDIA App\cef、WPS\office6）
    "cef", "cefsharp", "office6", "office12", "office14", "office15", "office16",
    "electron", "webview", "webview2", "dist", "build", "source", "src", "code",
    "module", "browserengine", "netmon",
}

# 运行时目录不参与「父进程认亲」：谁拉起的解释器不算谁的子应用。
# 踩过的坑：WorkBuddy 终端里跑一段 python，那个 python 的祖先进程就是
# WorkBuddy.exe，于是大肥鱼整个 venv 被并进 WorkBuddy 组，组名还变成 "scripts"。
RUNTIME_DIRS = {
    "venv", ".venv", "scripts", "virtualenv", "site-packages", "binaries",
    "python", "python3", "python310", "python311", "python312", "python313",
    "node", "nodejs", "npm", "git", "portablegit", "usr", "cmd",
    # 浏览器内核：一堆应用共用，谁都用它、它不属于任何一个。
    # 不挡的话 Clash Verge / OpenSpeedy 会被并成一组（踩过：组名和窗口标题
    # 指向两个不同软件，用户根本没法判断在关什么）。
    "edgewebview", "ebwebview", "webview2", "msedgewebview2",
}


def _is_runtime_dir(pdir):
    return os.path.basename(pdir).lower() in RUNTIME_DIRS


# 显示名。没有的就用目录名/进程名，用户也能对上。
FRIENDLY = {
    "weixin": "微信",
    "doubao": "豆包",
    "doubaoime": "豆包输入法",
    "workbuddy": "WorkBuddy",
    "desktoplite": "360 桌面助手",
    "sysdiag": "火绒安全",
    "appstore": "火绒软件商店",
    "clash verge": "Clash Verge",
    "wps office": "WPS Office",
    "kingsoft": "WPS Office",
    "nvidia app": "NVIDIA App",
    "nvcontainer": "NVIDIA 容器",
    "procexpcn": "Process Explorer",
    "procexpcn64": "Process Explorer",
    "edgewebview": "WebView2 内核",
    "python312": "Python 3.12",
    "python313": "Python 3.13",
    "baidunetdisk": "百度网盘",
    "jianyingpro": "剪映专业版",
    "jianyangzhuanyeban": "剪映专业版",
    "baiduwangpan": "百度网盘",
    "360speedtest": "360 宽带测速器",
    "quark": "夸克",
    "mubu": "幕布",
    "todesk": "ToDesk",
    "splayer": "SPlayer",
}

DEFAULT_CFG = {
    "min_mb": 40,                  # 整组内存低于这个数不折腾
    "respect_auto_restart": True,  # 杀完会自己回来 → 自动降级为「保护」，别再白折腾
    "protect_core": True,          # 锁定系统核心进程（面板上灰掉那些行）
    "apps": {},                    # {"weixin": "protect"}  应用 → 四态
    "whitelist": [],               # 旧字段：按进程名保护，仍生效，面板改动会同步清空
    "auto_restart": [],            # 自动学到的「杀不掉」应用
}


# ---------------------------------------------------------------- 配置
def load_cfg():
    """读配置，坏了/缺了都用默认值，绝不因为配置问题让功能挂掉。"""
    cfg = {k: (v.copy() if isinstance(v, (dict, list)) else v)
           for k, v in DEFAULT_CFG.items()}
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_CFG:
                if k in data and isinstance(data[k], type(DEFAULT_CFG[k])):
                    cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    """写回配置。只写认识的键，别把别的垃圾带进去。"""
    out = {}
    for k in DEFAULT_CFG:
        out[k] = cfg.get(k, DEFAULT_CFG[k])
    out["_怎么用"] = ("apps 里写「应用id: 状态」，状态四选一："
                     "protect=保护 / close=只关窗 / clean=可清理 / ignore=不管。"
                     "应用 id 就是设置面板那张表里显示的名字（不区分大小写）。")
    out["_阈值"] = "min_mb：整组内存低于这个数就不折腾。"
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def ensure_cfg():
    if CFG_PATH.exists():
        return
    save_cfg(dict(DEFAULT_CFG))


# ---------------------------------------------------------------- 窗口
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wt.LPARAM]
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowTextLengthW.argtypes = [wt.HWND]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

WM_CLOSE = 0x0010

# 这些标题不是给人看的：框架占位窗、输入法隐藏窗、桌面宿主。
# 采进「窗口标题」列只会让人更糊涂，采集时直接丢掉。
_TITLE_NOISE = {
    "program manager", "dummy layered wnd", "dummylayeredwnd",
    "default ime", "默认 ime", "msctfime ui", "gdi+ window",
    "hidden window", "tooltips_class32", "ime", "nvsphelper64",
}


def window_info():
    """pid → [(hwnd, 标题)]，只收「可见 + 顶层 + 有标题」的窗口。

    标题为空的多半是隐藏的辅助窗口，关掉既没用还可能弄坏托盘程序，
    所以关窗只认有标题的；再把框架占位窗的标题剔掉，免得表格里一堆
    dummyLayeredWnd。
    """
    out = defaultdict(list)

    def _cb(hwnd, _):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            ln = _user32.GetWindowTextLengthW(hwnd)
            if ln <= 0:
                return True
            buf = ctypes.create_unicode_buffer(ln + 1)
            _user32.GetWindowTextW(hwnd, buf, ln + 1)
            title = (buf.value or "").strip()
            if not title or title.lower() in _TITLE_NOISE:
                return True
            pid = wt.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                out[pid.value].append((hwnd, title))
        except Exception:                              # noqa: BLE001
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return out


def windows_by_pid():
    """pid → [hwnd]。老接口，只留编号。"""
    return {pid: [h for h, _t in v] for pid, v in window_info().items()}


# ---------------------------------------------------------------- 归组
def _norm(name):
    return (name or "").strip().lower()


def _stem(name):
    n = _norm(name)
    return n[:-4] if n.endswith(".exe") else n


def _pretty_stem(name):
    """进程名收拾成人愿意看的样子。

    有些包的 exe 名长得像下载站标题
    （「图片去水印(Inpaint)7.2.x86中文特别版.exe」），直接摆进面板没法看，
    所以砍掉括号后的版本/架构和裸版本号。
    """
    raw = _stem(name)
    s = re.split(r"[\(\（]", raw)[0]
    s = re.sub(r"\d+(\.\d+)+", "", s).strip(" ._-")
    return (s or raw)[:16]


def _in_never(name):
    n = _norm(name)
    return n in NEVER or _stem(n) in NEVER


# ---- 系统级识别：面板上把「Windows 自己的东西」和「你开的软件」分开。
# 注意这跟 NEVER（不许杀的硬保护）不是一回事：火绒、WorkBuddy 受硬保护，
# 但它们是用户装的软件，不算系统级。
SYS_USERS = {
    "system", "local service", "network service", "localservice",
    "networkservice", "defaultaccount", "trustedinstaller",
    "font driver host", "window manager",
}


def is_system_proc(info):
    """这个进程算不算系统级。三条判据任一命中即算：

    1. 跑在系统账户下（SYSTEM / LOCAL SERVICE / NETWORK SERVICE / DWM-n）
    2. exe 落在 C:\\Windows 下
    3. 路径和账户都读不到 —— 提权服务或受保护进程，用户本来也管不了
    """
    user = (info.get("user") or "").lower()
    if user in SYS_USERS or user.startswith("dwm-"):
        return True
    exe = (info.get("exe") or "").lower()
    if exe.startswith("c:\\windows\\"):
        return True
    return not exe


def _looks_like_version(name):
    """1.2.3 / v1.0.0.23 / 22.22.2-6 这类都算版本层，要跳过。"""
    s = (name or "").strip().lower()
    if s[:1] in ("v", "r"):
        s = s[1:]
    if not s:
        return False
    # 允许 数字 . 数字 - 数字 的组合，但不能全是点
    return s.replace(".", "").replace("-", "").replace("_", "").isdigit()


# 顶层容器目录。拿它当「应用 id 的别名」毫无意义（每个软件都装在
# Program Files 下面），所以别名匹配要把它剔掉；而 bin / cef / office6 这类
# 产品内部的层**必须留着** —— 老配置里的 id 往往就是它们。
TOP_DIRS = {
    "program files", "program files (x86)", "windows", "users", "appdata",
    "local", "locallow", "roaming", "programdata", "common files",
    "public", "desktop", "documents", "downloads", "system32", "syswow64",
}


def _dir_alias(exe):
    """exe 所在目录往上三级的名字，用来认出「老配置里的应用 id」。

    分组规则一改，id 就会变（office6 → wps office、cef → nvidia app），
    用户之前设的「可清理」会凭空消失。这里把原始目录名都收着当别名，
    配置里记着哪个就认哪个 —— 靠它兜住，不用每次改规则都写迁移表。
    """
    out = []
    d = os.path.dirname(exe or "")
    for _ in range(3):
        b = os.path.basename(d)
        if not b:
            break
        nb = _norm(b)
        if len(nb) > 2 and nb not in TOP_DIRS and nb not in out:
            out.append(nb)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return out


def product_dir(exe):
    """从 exe 路径推产品根目录，跳过 bin/app/1.2.3 这类通用层。"""
    if not exe:
        return ""
    d = os.path.dirname(exe)
    for _ in range(4):
        base = os.path.basename(d)
        if not base:
            break
        looks_ver = _looks_like_version(base)
        if base.lower() in GENERIC_DIRS or looks_ver:
            parent = os.path.dirname(d)
            if parent and parent != d:
                d = parent
                continue
        break
    return d.lower()


def _is_system_dir(pdir):
    return pdir.startswith("c:\\windows")


def fresh():
    """拿到「磁盘上最新版」的自己。

    桌宠一开就是几个小时，sys.modules 里这份 cleaner 可能早就是旧的；改完代码
    不该要求用户重启。但也不能每次都硬 reload —— 测试里 monkeypatch 掉的函数
    会被重载冲掉，那就变成「测试以为在跑假数据，实际在真杀进程」（踩过一次，
    一次测试关掉了用户 39 个进程）。

    所以只在**文件 mtime 变了**才重载。第一次调用只把当前 mtime 认下来。
    """
    import importlib
    import sys
    mod = sys.modules.get(__name__)
    if mod is None:
        return sys.modules[__name__]
    try:
        mtime = os.path.getmtime(__file__)
    except OSError:
        return mod
    cur = getattr(mod, "_disk_mtime", None)
    if cur is None:
        mod._disk_mtime = mtime          # 头一次：认下当前版本，不重载
        return mod
    if cur == mtime:
        return mod
    try:
        mod = importlib.reload(mod)
        mod._disk_mtime = mtime
    except Exception:                    # noqa: BLE001  重载失败就用内存里那份
        pass
    return mod


def scan_apps():
    """返回应用列表，按内存从大到小。每个元素：

    {id, label, key, dirs, names, pids, hwnds, titles, mb, has_win,
     system, sys_count, mode, mode_text, locked, eligible, why}

    titles     该应用当前可见窗口的标题（已剔掉 dummy 类占位窗）
    system     True = 系统级（Windows 自身 / 提权后台服务），面板上可过滤
    sys_count  组里系统级进程的条数
    """
    if psutil is None:
        return []

    cfg = load_cfg()
    apps_cfg = {_norm(k): v for k, v in (cfg.get("apps") or {}).items()}
    white = {_norm(x) for x in (cfg.get("whitelist") or [])}
    white_stem = {_stem(w) for w in white}
    auto_rt = {_norm(x) for x in (cfg.get("auto_restart") or [])}
    respect_rt = bool(cfg.get("respect_auto_restart", True))
    protect_core = bool(cfg.get("protect_core", True))
    min_mb = float(cfg.get("min_mb") or 40)

    me = os.getpid()
    procs = {}
    for p in psutil.process_iter(["pid", "name", "ppid", "memory_info"]):
        try:
            pid = p.info["pid"]
            if pid == me:
                continue
            try:
                exe = p.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                exe = ""
            try:
                user = (p.username() or "").split("\\")[-1].strip().lower()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                user = ""      # 提权进程读不到，靠路径判
            procs[pid] = {
                "pid": pid,
                "name": _norm(p.info["name"]),
                "ppid": p.info.get("ppid") or 0,
                "mb": (p.info["memory_info"].rss
                       if p.info.get("memory_info") else 0) / 1048576.0,
                "pdir": product_dir(exe),
                "exe": exe,
                "user": user,
                "alist": _dir_alias(exe),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            continue

    # ---- 并查集：先按产品目录，再沿父进程链把同应用的不同目录收拢
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def key_of(pid):
        d = procs[pid]["pdir"]
        if not d:
            return "proc:" + procs[pid]["name"]
        if _is_system_dir(d):
            return "win:" + procs[pid]["name"]
        return "dir:" + d

    for pid in procs:
        find(key_of(pid))

    for pid, info in procs.items():
        my = key_of(pid)
        if not my.startswith("dir:"):
            continue
        mydir = info["pdir"]
        if _is_runtime_dir(mydir):
            continue                           # 解释器/运行时自己站一队
        cur = info["ppid"]
        for _ in range(10):                    # 最多往上找 10 层，防止环
            up = procs.get(cur)
            if not up:
                break
            updir = up["pdir"]
            if not updir or _is_system_dir(updir) or _is_runtime_dir(updir):
                break                          # 不穿过系统进程/解释器去找爹
            if updir != mydir:
                # 两者互为子目录时，浅的那个当根（父进程也可能在更深的子目录里跑，
                # 比如豆包输入法：ImeService 在 DoubaoIME，拉起它的 bootstrap 在
                # DoubaoIME\bootstrap，谁当爹不能只看进程关系）。
                if mydir.startswith(updir + os.sep):
                    union("dir:" + updir, my)
                elif updir.startswith(mydir + os.sep):
                    union(my, "dir:" + updir)
                else:
                    union("dir:" + updir, my)
                break
            cur = up["ppid"]

    wins = window_info()

    groups = {}
    for pid, info in procs.items():
        root = find(key_of(pid))
        g = groups.setdefault(root, {
            "key": root, "pids": [], "mb": 0.0, "names": defaultdict(int),
            "dirs": set(), "hwnds": [], "titles": [], "sys": 0, "top": None,
            "alist": [],
        })
        g["pids"].append(pid)
        g["mb"] += info["mb"]
        g["names"][info["name"]] += 1
        for al in info["alist"]:
            if al not in g["alist"]:
                g["alist"].append(al)
        if info["pdir"]:
            g["dirs"].add(info["pdir"])
        if is_system_proc(info):
            g["sys"] += 1
        # 记住组里内存最大的那个进程 —— 它的目录/名字最能代表这个应用
        if g["top"] is None or info["mb"] > g["top"]["mb"]:
            g["top"] = info
        for hwnd, title in wins.get(pid, []):
            g["hwnds"].append(hwnd)
            if title not in g["titles"]:
                g["titles"].append(title)

    out = []
    for root, g in groups.items():
        # 应用 id：优先用「主进程」（内存最大的那个）的产品目录叶子名。
        # 以前取「最短路径」，结果 Process Explorer 被叫成 filetemp（它把自己
        # 解压到 D:\temp\filetemp，路径反而更短）；主进程的名才是真名。
        top = g["top"] or {}
        tpdir = top.get("pdir") or ""
        if tpdir and not _is_system_dir(tpdir):
            base = os.path.basename(tpdir) or top.get("name", "")
        elif root.startswith("dir:"):
            base = os.path.basename(min(g["dirs"], key=len)) if g["dirs"] else ""
            base = base or os.path.basename(root[4:])
        else:
            # 系统进程按进程名站队：svchost 就叫 svchost，不能叫 system32
            base = root.split(":", 1)[1]
        base = base or "?"
        # 名字太泛（单字母、盘符、目录残片）就拿进程名兜底，别让人猜
        if g["dirs"] and (len(base) <= 2 or base.endswith(":\\")
                          or base.lower() in GENERIC_DIRS):
            base = _pretty_stem(top.get("name") or base) or base
        if g["dirs"]:
            # 中文名优先：叶子目录名对不上，就用主进程名对一次。
            # （Process Explorer 把自己解压到 D:\temp\filetemp 里跑，叶子名是
            #  filetemp，只有进程名 procexpcn64 认得出来。但**不能**扫全组的
            #  目录名 —— 一个组里可能挂着别的软件的目录，那样名字会张冠李戴。）
            label = (FRIENDLY.get(base.lower())
                     or FRIENDLY.get(_stem(top.get("name") or ""))
                     or base)
        else:
            label = FRIENDLY.get(base.lower()) or _stem(top.get("name") or base) or base
        app_id = _norm(base)

        names = dict(g["names"])
        has_win = bool(g["hwnds"])
        # 过半进程是系统级 → 整组算系统级（混组不会因为带了几个提权子进程就变色）
        is_sys = g["sys"] * 2 > len(g["pids"])

        # ---- 锁定：组里有任何进程名命中硬保护 → 整组保护且不可改
        locked = any(_in_never(n) for n in names)
        # ---- 组里有名字在旧版 whitelist 里 → 保护（可改，改的时候会清掉）
        in_white = any(n in white or _stem(n) in white_stem for n in names)

        mode = apps_cfg.get(app_id)
        if mode is None:
            # 分组规则改过，id 会变（office6 → wps office、cef → nvidia app）。
            # 顺序遍历 = 离 exe 近的目录优先，命中哪个算哪个。
            for al in list(g["alist"]) + [_stem(n) for n in g["names"]]:
                if al in apps_cfg:
                    mode = apps_cfg[al]
                    break
        mode = _norm(mode or MODE_IGNORE)
        if mode not in MODES:
            mode = MODE_IGNORE
        if locked:
            mode = MODE_PROTECT
        elif in_white and mode == MODE_IGNORE:
            mode = MODE_PROTECT
        if respect_rt and app_id in auto_rt:
            mode = MODE_PROTECT

        why = ""
        if locked:
            why = "系统核心，锁定"
        elif respect_rt and app_id in auto_rt:
            why = "杀完会自己回来"
        elif mode == MODE_IGNORE:
            why = "没设置"
        elif mode == MODE_PROTECT:
            why = "你设的白名单" if in_white else "你设的保护"
        elif mode == MODE_CLOSE:
            why = "有窗口可关" if has_win else "本来就没窗口"
        elif mode == MODE_CLEAN:
            if g["mb"] < min_mb:
                why = f"不到 {min_mb:.0f}MB"
            elif has_win:
                why = "会先关窗再结束"
            else:
                why = "待清理"

        # 够不够格真动手：状态是「可清理」且过了内存门槛。
        # 注意这里不再看窗口 —— 用户把它设成可清理，就是授权连窗口一起关。
        # （2026-11-20 用户实测：开了十几个程序设成可清理，点了一下一个都没关，
        #   因为旧规则「有窗口就不动」把它们全挡住了。）
        eligible = (mode == MODE_CLEAN and g["mb"] >= min_mb)

        out.append({
            "id": app_id,
            "label": label,
            "key": root,
            "dirs": sorted(g["dirs"]),
            "names": names,
            "pids": list(g["pids"]),
            "hwnds": list(g["hwnds"]),
            "titles": list(g["titles"]),
            "mb": round(g["mb"], 1),
            "has_win": has_win,
            "system": is_sys,
            "sys_count": g["sys"],
            "mode": mode,
            "mode_text": MODE_TEXT.get(mode, mode),
            "eligible": eligible,
            "locked": bool(locked and protect_core),
            "why": why,
        })

    out.sort(key=lambda a: -a["mb"])
    return out


# ---------------------------------------------------------------- 改状态
def set_mode(app_id, mode):
    """给某个应用设状态。返回 (ok, 说明)。

    · 系统核心应用：protect_core 开着时锁死；关掉也只能到「只关窗」，不许杀。
    · 设成非 protect 时，把它的进程名从旧 whitelist 里摘掉，避免两处打架。
    """
    if mode not in MODES:
        return False, f"没有这个状态：{mode}"
    cfg = load_cfg()
    key = _norm(app_id)
    app = next((a for a in scan_apps() if a["id"] == key), None)
    if app is None:
        return False, f"没找到应用：{app_id}"

    # 是不是核心应用，和「保护核心进程」这个开关无关 —— 开关只管能不能关窗，
    # 绝不能因为它关掉就允许结束进程（2026-11-20 自检抓到这条漏了）。
    is_core = any(_in_never(n) for n in app["names"])
    if is_core and mode == MODE_CLEAN:
        return False, "这是系统核心，只能保护或只关窗，不能结束进程"
    if is_core and cfg.get("protect_core", True) and mode != MODE_PROTECT:
        return False, "已被「保护核心进程」锁住，先关掉那个开关"

    apps = dict(cfg.get("apps") or {})
    apps[key] = mode
    cfg["apps"] = apps
    if mode != MODE_PROTECT:
        names = {_norm(n) for n in app["names"]}
        stems = {_stem(n) for n in names}
        cfg["whitelist"] = [w for w in (cfg.get("whitelist") or [])
                            if _norm(w) not in names and _stem(w) not in stems]
    if mode != MODE_PROTECT:
        cfg["auto_restart"] = [x for x in (cfg.get("auto_restart") or [])
                               if _norm(x) != key]
    save_cfg(cfg)
    return True, f"{app['label']} → {MODE_TEXT[mode]}"


# ---------------------------------------------------------------- 执行
def _close_windows(hwnds):
    """发 WM_CLOSE，让程序自己关窗口（能触发保存/退出逻辑，不硬来）。"""
    n = 0
    for hwnd in hwnds:
        try:
            if _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
                n += 1
        except Exception:                          # noqa: BLE001
            pass
    return n


def run(dry=False):
    """执行一次释放。返回结果字典，失败也不抛异常。"""
    if psutil is None:
        return {"ok": False, "error": "没装 psutil", "freed_mb": 0,
                "killed": [], "closed": 0, "count": 0, "revived": []}

    ensure_cfg()
    cfg = load_cfg()
    t0 = time.time()
    apps = scan_apps()

    to_close = [a for a in apps
                if a["mode"] in (MODE_CLOSE, MODE_CLEAN) and a["hwnds"]]
    to_kill = [a for a in apps if a["eligible"]]

    plan = {
        "close": [(a["label"], len(a["hwnds"])) for a in to_close],
        "kill": [(a["label"], len(a["pids"]), a["mb"]) for a in to_kill],
        "mb": round(sum(a["mb"] for a in to_kill), 1),
    }
    if dry:
        return {"ok": True, "dry": True, "freed_mb": plan["mb"], "plan": plan,
                "closed": sum(n for _, n in plan["close"]),
                "count": sum(n for _, n, _ in plan["kill"]),
                "killed": [x[0] for x in plan["kill"]],
                "revived": [], "stuck": [], "secs": round(time.time() - t0, 2)}

    before = psutil.virtual_memory().available

    # 第一步：关窗（「只关窗」和「可清理」都要先关）。发 WM_CLOSE 而不是硬杀，
    # 程序能走自己的退出逻辑。
    closed = 0
    for a in to_close:
        closed += _close_windows(a["hwnds"])
    if to_close:
        time.sleep(0.9)                            # 给它一点时间响应关窗

    # 第二步：复查。窗口真关掉了才轮到结束进程；还赖着的说明有「是否保存」
    # 这类对话框，硬杀会丢数据，跳过并如实报出来。
    now = {a["id"]: a for a in scan_apps()} if to_kill else {}
    killed, dead_pids, stuck = [], [], []
    for a in to_kill:
        cur = now.get(a["id"])
        if a["hwnds"] and cur and cur["has_win"]:
            stuck.append(a["label"])
            continue
        pids = (cur["pids"] if cur else [])
        if not pids:
            continue                               # 窗口一关它自己就走了，没问题
        hit = []
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
                hit.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                continue
        if hit:
            killed.append(a["label"])
            dead_pids.extend(hit)

    if dead_pids:
        time.sleep(0.6)
        for pid in dead_pids:
            try:
                psutil.Process(pid).kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                pass

    after = psutil.virtual_memory().available
    freed = max(0.0, (after - before) / 1048576.0)

    # ---- 复查：会自己回来的，记下来，以后不碰
    revived = []
    if killed and cfg.get("respect_auto_restart", True):
        time.sleep(2.2)
        back = {a["id"] for a in scan_apps()}
        for label in killed:
            app = next((a for a in apps if a["label"] == label), None)
            if (app and app["id"] in back
                    and app["id"] not in (cfg.get("auto_restart") or [])):
                revived.append(label)
                cfg["auto_restart"] = list(cfg.get("auto_restart") or []) + [app["id"]]
                apps_cfg = dict(cfg.get("apps") or {})
                apps_cfg[app["id"]] = MODE_PROTECT
                cfg["apps"] = apps_cfg
        if revived:
            save_cfg(cfg)

    return {"ok": bool(killed or closed), "freed_mb": round(freed, 1),
            "freed_estimate": round(plan["mb"], 1), "killed": killed,
            "closed": closed, "count": len(dead_pids), "revived": revived,
            "stuck": stuck, "secs": round(time.time() - t0, 2), "dry": False}


def say_line(r):
    """给气泡用的一句话。"""
    if r.get("error"):
        return f"释放没干成：{r['error']}"
    mb = r.get("freed_mb") or 0
    n = r.get("count") or 0
    closed = r.get("closed") or 0
    revived = r.get("revived") or []
    stuck = r.get("stuck") or []

    parts = []
    if closed:
        parts.append(f"关了 {closed} 个窗口")
    if n:
        if mb >= 1024:
            parts.append(f"结束 {n} 个进程，腾出 {mb / 1024:.1f} GB")
        elif mb >= 1:
            parts.append(f"结束 {n} 个进程，腾出 {mb:.0f} MB")
        else:
            parts.append(f"结束 {n} 个进程")
    if not parts:
        if stuck:
            return f"{'、'.join(stuck)} 的窗口没关掉，我没敢硬杀。"
        return "没啥可收的，都挺干净。"
    line = "收工，" + "，".join(parts) + "。"
    if revived:
        line += f" {'、'.join(revived)}关不掉，几秒又回来了，我以后不碰它。"
    if stuck:
        line += f" {'、'.join(stuck)} 的窗口没关掉，我没敢硬杀。"
    return line


if __name__ == "__main__":
    import sys

    if "--apps" in sys.argv:
        for a in scan_apps():
            if a["mb"] < 1:
                continue
            flag = "窗" if a["has_win"] else "  "
            print(f"{a['label'][:16]:<18}{a['mb']:>8.0f}MB {len(a['pids']):>3}个 {flag} "
                  f"{a['mode_text']:<4} {a['why']}")
        sys.exit(0)

    dry = "--dry" in sys.argv
    res = run(dry=dry)
    if dry:
        p = res["plan"]
        print("=== 只关窗 ===")
        for label, n in p["close"]:
            print(f"  {label:<20} {n} 个窗口")
        print("=== 结束进程 ===")
        for label, n, mb in p["kill"]:
            print(f"  {label:<20} {n} 个进程 {mb:>8.0f} MB")
        print(f"  合计 {p['mb']:.0f} MB")
    else:
        print(say_line(res))
        print(json.dumps(res, ensure_ascii=False, indent=2))

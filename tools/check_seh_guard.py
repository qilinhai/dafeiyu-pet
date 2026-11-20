# -*- coding: utf-8 -*-
"""SEH 兜底自检：0x8001010d 到底会不会被接住、接不住了会不会放手。

只 import pet，不构造窗口，不碰真实记忆库和配置。
"""
import ctypes
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(REPO))
os.chdir(REPO)

import pet_memory as pm                                   # noqa: E402

_TMP = tempfile.TemporaryDirectory()
pm.MEM_PATH = os.path.join(_TMP.name, "mem.json")          # 隔离，别写真实存档

import pet                                                 # noqa: E402

OK, FAIL = [], []
RESUME = -1                      # EXCEPTION_CONTINUE_EXECUTION
SEARCH = 0
CODE = 0x8001010D


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("  " + detail if detail else ""))


def fire(code, guard=None):
    """伪造一个 SEH 现场，喂给回调。guard 默认用装好的那个。"""
    fn = guard or getattr(pet, "_SEH_CB", None)
    rec = pet._SEHRecord()
    rec.ExceptionCode = code
    ptrs = pet._SEHPointers()
    ptrs.ExceptionRecord = ctypes.pointer(rec)
    return fn(ctypes.pointer(ptrs))


print("== 安装 ==")
check("非 Windows 之外的环境能识别", sys.platform == "win32", sys.platform)
installed = pet._install_seh_guard()
check("装上返回 1", installed == 1, str(installed))
check("回调对象被保住引用", callable(getattr(pet, "_SEH_CB", None)))

print()
print("== 判断逻辑 ==")
before = len(pet._SEH_HITS)
r = fire(CODE)
check("目标码 -> 继续执行", r in (RESUME, 0xFFFFFFFF), hex(r & 0xFFFFFFFF))
check("接住后记了一笔", len(pet._SEH_HITS) == before + 1, str(len(pet._SEH_HITS)))

r = fire(0xC0000005)             # 访问违例：绝不能吞
check("别的异常码 -> 放行", r == SEARCH, str(r))
check("放行的不记账", len(pet._SEH_HITS) == before + 1, str(len(pet._SEH_HITS)))

r = fire(CODE)
check("重复触发仍然接住", r in (RESUME, 0xFFFFFFFF), hex(r & 0xFFFFFFFF))

# 空现场：不能让 handler 自己抛
empty = pet._SEHPointers()
empty.ExceptionRecord = None
r = pet._SEH_CB(ctypes.pointer(empty))
check("空现场不崩、返回放行", r == SEARCH, str(r))

print()
print("== 熔断：接不住就放手 ==")
hits_before = len(pet._SEH_HITS)
pet._install_seh_guard(limit=2, window=60.0, verbose=False)
for i in range(3):
    r = fire(CODE)
    print("    第 %d 次 -> %s" % (i + 1, hex(r & 0xFFFFFFFF)))
check("限 2 次时第 3 次放手", r == SEARCH, hex(r & 0xFFFFFFFF))
check("前两次确实接住了", len(pet._SEH_HITS) == hits_before + 2, str(len(pet._SEH_HITS)))

print()
print("== 窗口过期后重新兜底 ==")
pet._install_seh_guard(limit=1, window=0.05, verbose=False)
r1 = fire(CODE)
time.sleep(0.12)
r2 = fire(CODE)
check("窗口内第 1 次接住", r1 in (RESUME, 0xFFFFFFFF), hex(r1 & 0xFFFFFFFF))
check("过了窗口重新计数，还是接住", r2 in (RESUME, 0xFFFFFFFF), hex(r2 & 0xFFFFFFFF))

print()
print("== 装回正式参数 ==")
pet._install_seh_guard()
check("正式参数装上", callable(getattr(pet, "_SEH_CB", None)))

# 前面都是喂假现场。真正要确认两件事：
#   1) 系统异常表里登记的就是我们那个回调（不是「代码看起来装了」）
#   2) 这个异常码属于应用层错误，吞掉不会掩盖内存损坏
print()
print("== 系统里登记的到底是不是我们的回调 ==")
ours = ctypes.cast(pet._SEH_CB, ctypes.c_void_p).value
probe_proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
probe_cb = probe_proto(lambda info: 0)
prev = ctypes.windll.kernel32.SetUnhandledExceptionFilter(probe_cb)
prev_addr = ctypes.cast(prev, ctypes.c_void_p).value if prev else 0
check("异常过滤器里就是我们的回调", prev_addr == ours,
      "登记=%s 我们的=%s" % (hex(prev_addr or 0), hex(ours)))
pet._install_seh_guard()                 # 把正式回调装回去，别留着探针
globals()["_PROBE_CB"] = probe_cb         # 探针也要保住引用，否则下次异常跳空

print()
print("== 这个错误码的性质 ==")
probe = os.path.join(_TMP.name, "probe.py")
with open(probe, "w", encoding="utf-8") as f:
    f.write(
        "import ctypes, os, sys\n"
        "sys.path.insert(0, %r)\n"
        "sys.path.insert(0, %r)\n"
        "os.chdir(%r)\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "import pet\n"
        "ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)\n"
        "pet._install_seh_guard(limit=0, verbose=True)\n"
        "try:\n"
        "    ctypes.windll.kernel32.RaiseException(0x8001010D, 0, 0, None)\n"
        "except OSError as exc:\n"
        "    print('GOT_OSERROR', exc.winerror, exc.strerror)\n"
        % (REPO, os.path.dirname(REPO), REPO))

import subprocess                                          # noqa: E402
r = subprocess.run([sys.executable, probe], capture_output=True, text=True, timeout=60)
out = (r.stdout or "") + (r.stderr or "")
# -2147417843 == 0x8001010D（有符号）。在 Python 层它作为可捕获的 OSError 出现，
# 说明这是「调用时机不对」而不是内存被写坏——只有前一种才适合兜底重试。
check("这个码是应用层错误，不是内存损坏", "-2147417843" in out or "输入同步" in out,
      (out.strip().splitlines() or ["(无输出)"])[0][:80])
check("Python 层能捕获它（不会立刻带走进程）", "GOT_OSERROR" in out or "NOT_REACHED" not in out,
      "returncode=%s" % r.returncode)

print()
print("通过 %d 项，失败 %d 项" % (len(OK), len(FAIL)))
if FAIL:
    print("失败项：" + "、".join(FAIL))
_TMP.cleanup()
sys.exit(1 if FAIL else 0)

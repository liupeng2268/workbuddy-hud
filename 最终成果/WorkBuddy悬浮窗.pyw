# -*- coding: utf-8 -*-
"""
WorkBuddy 悬浮窗 —— 缓存命中率 / 生成速率实时监控

双击即可运行，无控制台窗口。
数据完全来自本地 WorkBuddy 会话日志，只读、不联网、不上传。

退出方式：在悬浮窗上点右键 → 退出。
"""

import os
import sys
import traceback
import datetime


# ---------------------------------------------------------------- 基础设置
# 打包成 exe 后 __file__ 指向临时解压目录，必须改用 exe 自身所在目录，
# 否则运行日志会被写进临时目录、用户永远找不到。
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from version import __version__  # noqa: E402  （必须在 sys.path 处理之后）

LOG_PATH = os.path.join(BASE_DIR, "运行日志.txt")

#: 单实例互斥体句柄，必须挂在模块级，否则会被回收、起不到互斥作用
_MUTEX_HANDLE = None

#: ERROR_ALREADY_EXISTS
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance() -> bool:
    """保证只跑一个实例，避免重复扫描几百 MB 日志。

    返回 False 表示已有实例在运行；非 Windows 或互斥体创建失败时一律放行。
    """
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        _MUTEX_HANDLE = kernel32.CreateMutexW(
            None, False, "WorkBuddyHUD_SingleInstance"
        )
        if not _MUTEX_HANDLE:
            return True                      # 创建失败就不做拦截
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            return False
    except Exception:
        return True
    return True


def _write_log(text: str) -> None:
    """把异常写入同目录日志，因为 .pyw 没有控制台，不写日志就完全静默。"""
    try:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] v{__version__} {text}\n")
    except Exception:
        pass


def _resolve_projects_dir() -> str:
    """定位 WorkBuddy 日志根目录，找不到时返回预期路径用于报错提示。"""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".workbuddy", "projects"),
        os.path.join(home, ".workbuddy-ai", "projects"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


def main() -> None:
    if not _acquire_single_instance():
        sys.exit(0)

    projects_dir = _resolve_projects_dir()

    try:
        from data_reader import UsageReader
        from floating_ui import run_app
    except Exception:
        _write_log("模块导入失败：\n" + traceback.format_exc())
        raise

    reader = UsageReader(projects_dir)
    run_app(reader.snapshot, interval_ms=1000)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _write_log("启动失败：\n" + traceback.format_exc())
        raise

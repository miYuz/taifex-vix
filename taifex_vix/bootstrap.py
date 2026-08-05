# -*- coding: utf-8 -*-
"""anaconda 在非 Anaconda Prompt 環境下 `import ssl` 會炸的修補。

症狀:直接呼叫 C:\\Users\\Alrami\\anaconda3\\python.exe 時
    ImportError: DLL load failed while importing _ssl
原因:conda 的 OpenSSL DLL 放在 <prefix>\\Library\\bin,只有 conda activate
     才會進 PATH。排程/一般 shell 直接叫 python.exe 就找不到。
對策:import ssl 失敗時,把 conda 的 DLL 目錄用 os.add_dll_directory() 掛上再試。

任何會用到 HTTPS 的模組都要先 import 這支(config.py 已代為呼叫)。
"""
import os
import sys

_DLL_SUBDIRS = (
    "Library/bin",
    "Library/mingw-w64/bin",
    "Library/usr/bin",
    "DLLs",
    "bin",
)

_applied = False


def ensure_ssl():
    """確保 ssl 可 import。回傳 (ok, note)。"""
    global _applied
    try:
        import ssl  # noqa: F401
        return True, "ssl ok"
    except ImportError as first_err:
        if os.name != "nt":
            return False, f"ssl 不可用: {first_err}"

    added = []
    for sub in _DLL_SUBDIRS:
        d = os.path.join(sys.prefix, *sub.split("/"))
        if not os.path.isdir(d):
            continue
        try:
            os.add_dll_directory(d)     # py3.8+ Windows
            added.append(d)
        except (AttributeError, OSError):
            pass
        # PATH 也補一份:部分 DLL 是被相依 DLL 間接載入的
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    try:
        import ssl  # noqa: F401
        _applied = True
        return True, f"ssl 修補成功 (加掛 {len(added)} 個 DLL 目錄)"
    except ImportError as e:
        return False, (f"ssl 修補失敗: {e}; 已嘗試 {added}; "
                       f"請改從 Anaconda Prompt 執行")


def applied():
    return _applied

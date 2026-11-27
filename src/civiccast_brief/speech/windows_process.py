"""Keep console-child windows hidden when the desktop app has no console."""

import subprocess
import sys
from pathlib import Path


def hidden_process_kwargs() -> dict[str, int]:
    """Leave ordinary CLI launches and other operating systems unchanged."""
    if sys.platform == "win32" and (
        Path(sys.executable).name.lower() == "pythonw.exe"
        or sys.stdout is None
        or sys.stderr is None
    ):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def parent_alive(pid: int) -> bool:
    """Whether the process with ``pid`` is still running.

    Windows stops a scheduled task's own process but leaves a python child it started to run
    with no console, where every child it starts fails with 0xC0000142. A handle that cannot be
    opened counts as gone. Other systems always answer True, since nothing stops the caller's
    task this way."""
    if sys.platform != "win32":
        return True
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def streaming_python() -> str:
    """A pythonw parent needs the console interpreter for worker stdio."""
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    if executable.name.lower() == "pythonw.exe" and console.is_file():
        return str(console)
    return sys.executable

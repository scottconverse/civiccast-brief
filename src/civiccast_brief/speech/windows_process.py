"""Keep console-child windows hidden when the desktop app has no console."""

import os
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


def task_parent_pid() -> int:
    """The pid of the process the task scheduler started, past a virtual environment's launcher.

    A venv's ``python.exe`` starts the real interpreter as its child and waits for it, so the
    interpreter's own parent is the launcher, which outlives the task. Running under a launcher,
    step once past it to the launcher's parent; anywhere else, the parent is the answer."""
    parent = os.getppid()
    if sys.platform != "win32":
        return parent
    executable = sys.executable
    base = getattr(sys, "_base_executable", executable)
    if os.path.normcase(executable) == os.path.normcase(base):
        return parent
    import ctypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    th32cs_snapprocess = 0x2
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    except OSError:
        return parent
    if not snapshot or snapshot == invalid_handle:
        return parent
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32ProcessID == parent:
                name = Path(executable).name
                if entry.szExeFile.lower() == name.lower():
                    return int(entry.th32ParentProcessID)
                return parent
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return parent
    finally:
        kernel32.CloseHandle(snapshot)


def streaming_python() -> str:
    """A pythonw parent needs the console interpreter for worker stdio."""
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    if executable.name.lower() == "pythonw.exe" and console.is_file():
        return str(console)
    return sys.executable

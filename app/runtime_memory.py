"""Conservative Windows EXE memory policy; the unfrozen Python app is unchanged.

An 8 GB PC should retain headroom for Windows and its desktop.  The native
Job Object process-commit cap is a *last-resort* guard, not a substitute for
loading fewer models.  A cap breach can abort a native inference call; this is
preferable to exhausting the whole machine, but it is not crash-proof.
"""
from __future__ import annotations

import os
import sys
from typing import NamedTuple

GIB = 1024 ** 3
_JOB_HANDLE = None


class MemoryStatus(NamedTuple):
    total_bytes: int
    available_bytes: int


def windows_memory_status() -> MemoryStatus | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
        kernel32.GlobalMemoryStatusEx.restype = ctypes.c_int
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return MemoryStatus(int(stat.ullTotalPhys), int(stat.ullAvailPhys))
    except (OSError, AttributeError, ValueError):
        return None


def is_low_memory_frozen() -> bool:
    """Only alter the EXE on Windows with at most 10 GiB physical RAM."""
    if not getattr(sys, "frozen", False):
        return False
    stat = windows_memory_status()
    return stat is not None and stat.total_bytes <= 10 * GIB


def log_memory(stage: str) -> None:
    """Record free physical memory and process private committed memory."""
    if not getattr(sys, "frozen", False):
        return
    stat = windows_memory_status()
    if stat is None:
        return
    private = "unknown"
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            SIZE_T = ctypes.c_size_t

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", SIZE_T),
                    ("WorkingSetSize", SIZE_T),
                    ("QuotaPeakPagedPoolUsage", SIZE_T),
                    ("QuotaPagedPoolUsage", SIZE_T),
                    ("QuotaPeakNonPagedPoolUsage", SIZE_T),
                    ("QuotaNonPagedPoolUsage", SIZE_T),
                    ("PagefileUsage", SIZE_T),
                    ("PeakPagefileUsage", SIZE_T),
                    ("PrivateUsage", SIZE_T),
                ]

            counter = PROCESS_MEMORY_COUNTERS_EX()
            counter.cb = ctypes.sizeof(counter)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
                private = f"{counter.PrivateUsage / GIB:.2f} GiB"
        except (OSError, AttributeError, ValueError):
            pass
    print(
        f"[RAM] {stage}: process_private={private}; "
        f"physical_available={stat.available_bytes/GIB:.2f} GiB; "
        f"physical_total={stat.total_bytes/GIB:.2f} GiB",
        flush=True,
    )


def ensure_available_memory(stage: str, *, minimum_gib: float) -> None:
    if not getattr(sys, "frozen", False):
        return
    stat = windows_memory_status()
    if stat is None:
        return
    if stat.available_bytes < minimum_gib * GIB:
        raise MemoryError(
            f"Không đủ RAM trống để {stage}: còn "
            f"{stat.available_bytes / GIB:.1f} GB, cần tối thiểu "
            f"{minimum_gib:.1f} GB trước bước này. "
            "SoundText sẽ dừng bước tải để bảo vệ Windows. "
            "Hãy đóng ứng dụng khác hoặc chọn 'Chỉ ghi lời thoại'."
        )


def install_windows_memory_cap() -> bool:
    """Install a ~5 GiB per-process commit cap for 8 GB frozen Windows PCs.

    Windows Job Object enforces the limit across native Python/C++ allocators.
    Call *before* importing Torch/ONNX.  A failed installation is logged and
    is not misrepresented as successful protection.
    """
    global _JOB_HANDLE
    if not is_low_memory_frozen():
        return False
    if _JOB_HANDLE is not None:
        return True
    import ctypes
    from ctypes import wintypes

    SIZE_T = ctypes.c_size_t
    LARGE_INTEGER = ctypes.c_int64
    ULONG_PTR = SIZE_T

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", LARGE_INTEGER),
            ("PerJobUserTimeLimit", LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", SIZE_T),
            ("MaximumWorkingSetSize", SIZE_T),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", SIZE_T),
            ("JobMemoryLimit", SIZE_T),
            ("PeakProcessMemoryUsed", SIZE_T),
            ("PeakJobMemoryUsed", SIZE_T),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        print(f"[RAM WARN] Cannot create safety job: WinError {ctypes.get_last_error()}", flush=True)
        return False

    # 5 GiB process private commit leaves headroom for a typical 8 GiB Windows
    # desktop.  This is intentionally *not* the OS working-set limit.
    limit = min(5 * GIB, int(windows_memory_status().total_bytes * 0.65))
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00000100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = limit
    try:
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(f"SetInformationJobObject: WinError {ctypes.get_last_error()}")
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise OSError(f"AssignProcessToJobObject: WinError {ctypes.get_last_error()}")
    except OSError as exc:
        kernel.CloseHandle(job)
        print(f"[RAM WARN] Process cap not installed: {exc}", flush=True)
        return False
    _JOB_HANDLE = job  # Keep handle alive for process lifetime.
    print(f"[RAM SAFE] Windows process commit cap: {limit / GIB:.2f} GiB", flush=True)
    return True

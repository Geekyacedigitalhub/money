"""Pre-flight check for machines that are too small to render video.

A 3 GB server renders fine *if* Windows can borrow memory from disk. Without that,
the process is killed mid-render and the user sees nothing useful — no traceback,
no error, just a video that never appears. That is the single worst failure mode
on cheap VPS plans, and it is entirely detectable before starting.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class Memory:
    total_ram_mb: int
    total_pagefile_mb: int

    @property
    def headroom_mb(self) -> int:
        """RAM plus the pagefile — what a render can actually draw on."""
        return self.total_ram_mb + self.total_pagefile_mb


def windows_memory() -> Memory | None:
    """Physical RAM and total pagefile, or None when not measurable."""
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

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError, ValueError):
        return None

    megabyte = 1024 * 1024
    # ullTotalPageFile counts RAM as well, so the pagefile alone is the difference.
    pagefile_mb = max(0, int(status.ullTotalPageFile // megabyte) - int(
        status.ullTotalPhys // megabyte
    ))
    return Memory(
        total_ram_mb=int(status.ullTotalPhys // megabyte),
        total_pagefile_mb=pagefile_mb,
    )


# A 60-second 1080p render with Whisper `small` peaks near 1.8 GB. Below this much
# headroom it will usually die, and always dies when the pagefile is tiny.
MIN_HEADROOM_MB = 3500


def warnings(memory: Memory | None = None) -> list[str]:
    """Sentences to show before a render starts. Empty means 'go ahead'."""
    memory = memory if memory is not None else windows_memory()
    if memory is None:
        return []

    notes: list[str] = []
    if memory.headroom_mb >= MIN_HEADROOM_MB:
        return notes

    if memory.total_pagefile_mb < 2048:
        notes.append(
            f"only {memory.total_ram_mb} MB of RAM and "
            f"{memory.total_pagefile_mb} MB of pagefile — a render will be killed "
            "part-way with no error"
        )
        notes.append(
            "run this, then restart the machine:\n"
            "    Get-CimInstance Win32_PageFileSetting | Set-CimInstance "
            "-Property @{InitialSize=2048; MaximumSize=8192}"
        )
    else:
        notes.append(
            f"{memory.total_ram_mb} MB of RAM is tight for video rendering — "
            "keep other programs closed and expect it to be slow"
        )
    return notes

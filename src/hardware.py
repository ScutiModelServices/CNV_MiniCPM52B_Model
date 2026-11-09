"""What this machine (or container) can give a model: free RAM, CPU cores, and the NVIDIA GPU if there is one.

Read with the standard library only (no psutil): `nvidia-smi` for the GPU, `/proc/meminfo` or Windows' `GlobalMemoryStatusEx`
for memory. Every field is None when it cannot be read, and callers must treat None as "unknown, do not claim it fits".
The same file is in every model service that offers model variants; keep them identical."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from typing import Any


def _ram_mb() -> tuple[int | None, int | None]:
    """(total, available) in MB."""
    try:
        if os.name == "nt":
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tpage", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong),
                            ("tvirt", ctypes.c_ulonglong), ("avirt", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

            status = Status(length=ctypes.sizeof(Status))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.total // 2**20, status.avail // 2**20
        values = {}
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                key, _, rest = line.partition(":")
                values[key] = int(rest.split()[0]) // 1024  # kB to MB
        return values["MemTotal"], values["MemAvailable"]
    except (OSError, KeyError, ValueError, AttributeError):
        return None, None


def _gpu() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.free,compute_cap", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=True).stdout.splitlines()
        name, total, free, cap = [part.strip() for part in out[0].split(",")]
        return {"name": name, "vram_total_mb": int(total), "vram_free_mb": int(free), "compute_capability": cap}
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def describe(hardware: dict[str, Any] | None) -> str:
    """One line for the admin page; None means the model runs on a machine this service cannot see."""
    if hardware is None:
        return "The model runs on another machine, so its memory cannot be checked."
    free, gpu = hardware["ram_available_mb"], hardware["gpu"]
    memory = "memory unknown" if free is None else f"{free} MB of {hardware['ram_total_mb']} MB memory free"
    card = f"{gpu['name']} with {gpu['vram_free_mb']} MB of {gpu['vram_total_mb']} MB GPU memory free" if gpu else "no NVIDIA GPU"
    return f"This machine: {hardware['cpu_cores']} CPU cores, {memory}, {card}."


def probe() -> dict[str, Any]:
    total, available = _ram_mb()
    return {"cpu_cores": os.cpu_count(), "ram_total_mb": total, "ram_available_mb": available, "gpu": _gpu()}

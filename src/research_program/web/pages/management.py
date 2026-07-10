from __future__ import annotations

import ctypes
import os
import platform
import shutil
from pathlib import Path

import streamlit as st

from research_program.graph_workflow.storage import (
    INTERVAL_PER_DB_NAME,
    RAW_RUN_DB_NAME,
    get_storage_overview,
)
from research_program.web.utils import format_bytes


def render_management_page() -> None:
    st.header("その他管理")
    overview = get_storage_overview()
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("graph folders", overview["job_count"])
    col_b.metric("graph SQLite files", overview["sqlite_count"])
    col_c.metric("raw SQLite files", overview["raw_sqlite_count"])
    col_d.metric("PER SQLite files", overview.get("interval_per_sqlite_count", 0))

    st.subheader("Storage layout")
    st.code(
        "\n".join(
            [
                "outputs/graph_runs/<graph_type>/<graph_id>/",
                "  manifest.json",
                "  status.json",
                "  requests.json",
                "  graph_data.sqlite",
                f"  {INTERVAL_PER_DB_NAME}  # Interval PER calculated/aggregate data",
                f"  {RAW_RUN_DB_NAME}",
                "  figures/",
                "  logs/",
            ]
        ),
        language="text",
    )

    st.subheader("Server environment")
    system = get_system_status()
    cpu_col, mem_col, disk_col = st.columns(3)
    cpu_col.metric("CPU cores", system["cpu_count"])
    mem_col.metric("Memory", system["memory_label"])
    disk_col.metric("Disk free", system["disk_free_label"])
    st.json(
        {
            "platform": system["platform"],
            "python": system["python"],
            "machine": system["machine"],
            "processor": system["processor"],
            "working_directory": str(Path.cwd()),
            "memory": system["memory"],
            "disk": system["disk"],
        }
    )


def get_system_status() -> dict[str, object]:
    disk = shutil.disk_usage(Path.cwd())
    memory = get_memory_status()
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count() or 0,
        "memory": memory,
        "memory_label": format_memory_label(memory),
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "total": format_bytes(disk.total),
            "used": format_bytes(disk.used),
            "free": format_bytes(disk.free),
            "used_percent": round((disk.used / disk.total) * 100, 1) if disk.total else None,
        },
        "disk_free_label": format_bytes(disk.free),
    }


def get_memory_status() -> dict[str, object]:
    if platform.system().lower() != "windows":
        return {"available": False, "reason": "memory status is implemented for Windows in this GUI"}

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"available": False, "reason": "GlobalMemoryStatusEx failed"}

    used = status.ullTotalPhys - status.ullAvailPhys
    return {
        "available": True,
        "total_bytes": status.ullTotalPhys,
        "used_bytes": used,
        "available_bytes": status.ullAvailPhys,
        "total": format_bytes(status.ullTotalPhys),
        "used": format_bytes(used),
        "available_memory": format_bytes(status.ullAvailPhys),
        "used_percent": int(status.dwMemoryLoad),
    }


def format_memory_label(memory: dict[str, object]) -> str:
    if not memory.get("available"):
        return "unknown"
    return f"{memory['used_percent']}% / {memory['total']}"

"""Host-aware admission budget. Queued work stays durable when capacity is exhausted."""

import os
from pathlib import Path


class Capacity:
    def __init__(self, *, max_turns=None, memory_per_turn_mb=1024, reserve_memory_mb=512):
        if max_turns is not None and max_turns < 1:
            raise ValueError("max_turns must be positive")
        if memory_per_turn_mb < 1 or reserve_memory_mb < 0:
            raise ValueError("Invalid memory budget")
        self.max_turns = max_turns
        self.memory_per_turn = memory_per_turn_mb * 1024**2
        self.reserve_memory = reserve_memory_mb * 1024**2

    @staticmethod
    def resources():
        cpus = (
            len(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else (os.cpu_count() or 1)
        )
        available = None
        try:
            values = dict(
                line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
            )
            available = int(values["MemAvailable"].split()[0]) * 1024
        except (OSError, KeyError, ValueError):
            pass
        # Include the process's systemd/container group and all constrained ancestors.
        root = Path("/sys/fs/cgroup")
        groups = {root}
        try:
            for line in Path("/proc/self/cgroup").read_text().splitlines():
                hierarchy, controllers, relative = line.split(":", 2)
                if hierarchy == "0" and not controllers:
                    current = root / relative.lstrip("/")
                    if ".." not in current.parts:
                        groups.update(
                            parent
                            for parent in (current, *current.parents)
                            if parent == root or root in parent.parents
                        )
        except (OSError, ValueError):
            pass
        for group in groups:
            try:
                quota, period = (group / "cpu.max").read_text().split()
                if quota != "max":
                    cpus = min(cpus, max(1, int(quota) // int(period)))
            except (OSError, ValueError, ZeroDivisionError):
                pass
            try:
                limit = (group / "memory.max").read_text().strip()
                used = int((group / "memory.current").read_text())
                if limit != "max":
                    remaining = max(0, int(limit) - used)
                    available = remaining if available is None else min(available, remaining)
            except (OSError, ValueError):
                pass
        return cpus, available

    def snapshot(self, active=0, queued=0):
        cpus, available = self.resources()
        limit = cpus
        reason = "cpu"
        if self.max_turns is not None and self.max_turns < limit:
            limit, reason = self.max_turns, "configured ceiling"
        if available is not None:
            # Available memory already excludes active processes; count only new admissions.
            memory_limit = active + max(
                0, (available - self.reserve_memory) // self.memory_per_turn
            )
            if memory_limit < limit:
                limit, reason = memory_limit, "memory"
        return {
            "limit": limit,
            "active": active,
            "queued": queued,
            "reason": reason,
            "availableMemoryBytes": available,
            "cpuSlots": cpus,
        }

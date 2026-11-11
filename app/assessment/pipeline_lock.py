"""管道单实例锁：文件锁 + 元数据 + 陈旧锁安全清理。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from ..lock import InstanceLock


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:  # noqa: BLE001 - no Windows API on Linux/CI
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:  # noqa: BLE001
            # 无法确认时按存活处理，保证安全
            return True
        return True


class PipelineLock:
    """锁键 = election_id + period + mode。"""

    def __init__(
        self,
        lock_dir: Path,
        *,
        election_id: str,
        period_start: str,
        period_end: str,
        mode: str,
        stale_after_seconds: int = 3600,
    ):
        self.lock_dir = Path(lock_dir)
        self.stale_after_seconds = int(stale_after_seconds)
        self._election_id = election_id
        self._period_start = period_start
        self._period_end = period_end
        self._mode = mode
        self.key = f"{election_id}_{period_start}_{period_end}_{mode}"
        self.lock_path = self.lock_dir / f"tainan_assessment_{self.key}.lock"
        self.meta_path = self.lock_dir / f"tainan_assessment_{self.key}.json"
        self._instance: InstanceLock | None = None

    @property
    def lock_file(self) -> Path:
        return self.lock_path

    def _write_metadata(self) -> None:
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
            "period": f"{self._period_start}_{self._period_end}",
            "mode": self._mode,
            "election_id": self._election_id,
            "locked": True,
        }
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.meta_path)

    def _is_stale(self) -> bool:
        if not self.meta_path.exists():
            return False
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
        pid = int(meta.get("pid") or 0)
        if _pid_alive(pid):
            return False
        started_at = meta.get("started_at") or ""
        try:
            age = time.time() - datetime.fromisoformat(started_at).timestamp()
        except Exception:  # noqa: BLE001
            age = 0
        return age > self.stale_after_seconds

    def _clean_stale(self) -> None:
        for path in (self.lock_path, self.meta_path):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def acquire(self) -> bool:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        instance = InstanceLock(self.lock_path)
        if instance.acquire():
            self._instance = instance
            self._write_metadata()
            return True
        if self._is_stale():
            self._clean_stale()
            instance = InstanceLock(self.lock_path)
            if instance.acquire():
                self._instance = instance
                self._write_metadata()
                return True
        return False

    def release(self) -> None:
        if self._instance is not None:
            self._instance.release()
            self._instance = None
        try:
            if self.meta_path.exists():
                self.meta_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "PipelineLock":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()

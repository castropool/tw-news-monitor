"""Cross-process lock with Windows msvcrt and POSIX fcntl fallback."""

import os
from pathlib import Path

try:
    import msvcrt
except ImportError:  # Linux/CI compatibility
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None


def _lock(fd: int, mode: str, nbytes: int = 1) -> None:
    if msvcrt is not None:
        flag = msvcrt.LK_NBLCK if mode == "lock" else msvcrt.LK_UNLCK
        msvcrt.locking(fd, flag, nbytes)
        return
    if fcntl is not None:
        if mode == "lock":
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)


class InstanceLock:
    """Cross-process lock using file locking.

    The OS automatically releases the file lock when the owning process
    terminates, so there is no stale-lock problem after an abnormal exit.
    """

    def __init__(self, lock_path: str | Path):
        self._lock_path = Path(lock_path)
        self._fd: int | None = None
        self._acquired = False

    @property
    def path(self) -> Path:
        return self._lock_path

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True if successful."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(self._lock_path),
                os.O_CREAT | os.O_RDWR,
            )
            _lock(self._fd, "lock", 1)
            self._acquired = True
            pid_bytes = str(os.getpid()).encode("ascii")
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, pid_bytes)
            os.ftruncate(self._fd, len(pid_bytes))
            return True
        except (OSError, IOError):
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            return False

    def release(self) -> None:
        """Release the lock and clean up."""
        if self._fd is not None:
            try:
                if self._acquired:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    _lock(self._fd, "unlock", 1)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None
            self._acquired = False
        try:
            if self._lock_path.exists():
                self._lock_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()

    @property
    def acquired(self) -> bool:
        return self._acquired

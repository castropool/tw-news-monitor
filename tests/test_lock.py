import os
import tempfile
from pathlib import Path

import pytest

from app.lock import InstanceLock


class TestInstanceLock:
    def test_acquire_release(self):
        """Acquire and release a lock."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock = InstanceLock(lock_path)
            assert lock.acquire() is True
            assert lock.acquired is True
            lock.release()
            assert lock.acquired is False

    def test_two_locks_same_path(self):
        """Two locks on the same path cannot both be acquired."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock1 = InstanceLock(lock_path)
            lock2 = InstanceLock(lock_path)

            assert lock1.acquire() is True
            assert lock2.acquire() is False
            lock1.release()

            # After release, lock2 can acquire
            assert lock2.acquire() is True
            lock2.release()

    def test_context_manager(self):
        """Context manager acquires and releases."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            with InstanceLock(lock_path) as lock:
                assert lock.acquired is True
            assert lock.acquired is False

    def test_lock_file_removed_on_release(self):
        """Lock file is removed after release."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock = InstanceLock(lock_path)
            lock.acquire()
            assert lock_path.exists()
            lock.release()
            assert not lock_path.exists()

    def test_release_unacquired_lock(self):
        """Releasing an un-acquired lock does not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock = InstanceLock(lock_path)
            lock.release()

    def test_acquire_twice(self):
        """Acquiring the same lock twice works (after release)."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock = InstanceLock(lock_path)
            assert lock.acquire() is True
            lock.release()
            assert lock.acquire() is True
            lock.release()

    def test_lock_file_has_pid(self):
        """Lock file contains PID text (read from fd while held)."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            lock = InstanceLock(lock_path)
            lock.acquire()
            # Read from the open file descriptor (Path.read_text won't work
            # because the file is locked by msvcrt)
            os.lseek(lock._fd, 0, os.SEEK_SET)
            pid_bytes = os.read(lock._fd, 32)
            content = pid_bytes.decode("ascii").strip("\x00").strip()
            assert content.isdigit()
            lock.release()

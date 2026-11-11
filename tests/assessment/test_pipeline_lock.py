import json
import time
from datetime import datetime

from app.assessment.pipeline_lock import PipelineLock, _pid_alive
from app.lock import InstanceLock


class TestPipelineLock:
    def test_acquire_release(self, tmp_path):
        lock = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
        )
        assert lock.acquire() is True
        assert lock.lock_file.exists()
        lock.release()
        assert not lock.lock_file.exists()
        assert not lock.meta_path.exists()

    def test_blocks_concurrent_same_period(self, tmp_path):
        lock1 = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
        )
        lock2 = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
        )
        assert lock1.acquire() is True
        assert lock2.acquire() is False
        lock1.release()
        assert lock2.acquire() is True
        lock2.release()

    def test_different_mode_not_blocked(self, tmp_path):
        lock1 = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
        )
        lock2 = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="dry_run",
        )
        assert lock1.acquire() is True
        assert lock2.acquire() is True
        lock1.release()
        lock2.release()

    def test_stale_detection(self, tmp_path):
        lock = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
            stale_after_seconds=0,
        )
        lock.meta_path.write_text(
            json.dumps(
                {
                    "pid": 99999999,
                    "started_at": "2000-01-01T00:00:00",
                    "mode": "development",
                    "election_id": "tainan_mayoral_2026",
                }
            ),
            encoding="utf-8",
        )
        lock.lock_path.write_text("stale", encoding="utf-8")
        assert lock._is_stale() is True
        lock._clean_stale()
        assert not lock.meta_path.exists()
        assert not lock.lock_path.exists()

    def test_stale_lock_cleanup_then_acquire(self, tmp_path):
        lock = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
            stale_after_seconds=0,
        )
        # 持锁进程“已死”：锁文件存在但无 OS 锁（模拟陈旧残留）
        lock.lock_path.write_text("old", encoding="utf-8")
        lock.meta_path.write_text(
            json.dumps(
                {
                    "pid": 99999999,
                    "started_at": "2000-01-01T00:00:00",
                    "mode": "development",
                    "election_id": "tainan_mayoral_2026",
                }
            ),
            encoding="utf-8",
        )
        assert lock.acquire() is True
        lock.release()

    def test_pid_alive_helpers(self):
        assert _pid_alive(99999999) is False
        assert _pid_alive(-1) is False

    def test_metadata_fields(self, tmp_path):
        lock = PipelineLock(
            tmp_path,
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="production",
        )
        lock.acquire()
        meta = json.loads(lock.meta_path.read_text(encoding="utf-8"))
        assert meta["election_id"] == "tainan_mayoral_2026"
        assert meta["period"] == "2026-07-16_2026-07-31"
        assert meta["mode"] == "production"
        lock.release()

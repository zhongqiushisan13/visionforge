"""后台任务管理：一个窗口一次干一件重活，日志实时回传给界面。"""

from __future__ import annotations

import io
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class _Tee(io.TextIOBase):
    """把 print 的内容同时丢进日志缓冲区和原始 stdout。"""

    def __init__(self, job: "Job", original):
        self.job = job
        self.original = original
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        try:
            self.original.write(s)
            self.original.flush()
        except Exception:
            pass
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.job.add_log(line)
        return len(s)

    def flush(self) -> None:
        try:
            self.original.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False


@dataclass
class Job:
    id: str
    kind: str
    slug: str = ""
    status: str = "queued"          # queued / running / done / failed / cancelled
    created: float = field(default_factory=time.time)
    started: float = 0.0
    ended: float = 0.0
    logs: List[str] = field(default_factory=list)
    result: Any = None
    error: str = ""
    progress: Dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line[-500:])
            if len(self.logs) > 600:
                del self.logs[:100]

    def to_dict(self, since: int = 0) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id, "kind": self.kind, "slug": self.slug,
                "status": self.status,
                "elapsed": round((self.ended or time.time()) - (self.started or self.created), 1),
                "logs": self.logs[since:], "log_total": len(self.logs),
                "result": self.result, "error": self.error, "progress": self.progress,
            }


class JobManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vf-job")
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[Job], Any], slug: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, slug=slug, status="queued")
        with self._lock:
            self.jobs[job.id] = job
            # 只留最近 60 个任务，别让内存当垃圾场
            if len(self.jobs) > 60:
                for old in sorted(self.jobs.values(), key=lambda j: j.created)[:10]:
                    if old.status in ("done", "failed"):
                        self.jobs.pop(old.id, None)
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        import sys

        job.status = "running"
        job.started = time.time()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(job, old_out)      # type: ignore[assignment]
        sys.stderr = _Tee(job, old_err)      # type: ignore[assignment]
        try:
            job.result = fn(job)
            job.status = "done"
        except Exception as exc:
            job.status = "failed"
            job.error = f"{exc}"
            job.add_log("ERROR: " + str(exc))
            job.add_log(traceback.format_exc()[-900:])
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            job.ended = time.time()

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def active(self) -> List[Job]:
        return [j for j in self.jobs.values() if j.status in ("queued", "running")]

    def is_busy(self) -> bool:
        return bool(self.active())


MANAGER = JobManager()

"""
Background jobs stored in the database (no extra broker).

Uploading a scan enqueues an `analyze` job and returns at once; workers
(threads in the server, or `python -m app.manage worker`) claim jobs with
`SELECT ... FOR UPDATE SKIP LOCKED` on Postgres, so any number of workers
can run side by side. A claimed job holds a lease that the worker renews
while it makes progress: if the worker dies, the lease runs out and another
worker picks the job up. Failures are retried with backoff up to
`max_attempts`, then the job is marked failed with its error.
"""
import hashlib
import json
import logging
import os
import socket
import threading
import time
import traceback
from datetime import timedelta
from typing import Callable, Dict, List, Optional

from sqlalchemy import and_, delete, insert, or_, select, update

from app import db
from app.db import jobs

logger = logging.getLogger(__name__)

LEASE_SECONDS = 300
RETRY_BASE_SECONDS = 30
KEEP_FINISHED_DAYS = 14
ACTIVE = ("queued", "running")

_handlers: Dict[str, Callable[[dict, Callable[[int, int], None]], Optional[dict]]] = {}


def handler(kind: str):
    """Register the function that runs jobs of `kind`: fn(payload, progress) -> result dict."""
    def register(fn):
        _handlers[kind] = fn
        return fn
    return register


def _as_dict(row) -> Optional[dict]:
    if row is None:
        return None
    data = dict(row._mapping)
    data.pop("payload", None)
    for key in ("created_at", "run_after", "started_at", "finished_at", "lease_until"):
        if data.get(key) is not None:
            value = data[key]
            data[key] = (value if value.tzinfo else value.replace(tzinfo=db.utcnow().tzinfo)).isoformat()
    data.pop("dedupe_key", None)
    return data


# ---------------------------------------------------------------- producing

def enqueue(kind: str, payload: dict, repo: str = None, commit: str = None, max_attempts: int = 3):
    """Queue a job; an identical job that is still queued or running is returned instead. -> (job, created)"""
    key = hashlib.sha256(json.dumps([kind, payload], sort_keys=True, default=str).encode()).hexdigest()
    with db.engine().begin() as conn:
        with db.lock(conn, f"enqueue:{key}"):
            existing = conn.execute(
                select(jobs).where(jobs.c.dedupe_key == key, jobs.c.status.in_(ACTIVE)).order_by(jobs.c.id.desc()).limit(1)
            ).first()
            if existing is not None:
                return _as_dict(existing), False
            now = db.utcnow()
            job_id = conn.execute(insert(jobs).values(
                kind=kind, repo=repo, commit=commit, dedupe_key=key, payload=payload, status="queued",
                attempts=0, max_attempts=max_attempts, progress_done=0, progress_total=0,
                created_at=now, run_after=now,
            ).returning(jobs.c.id)).scalar_one()
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).first()
    return _as_dict(row), True


def get(job_id: int) -> Optional[dict]:
    with db.engine().connect() as conn:
        return _as_dict(conn.execute(select(jobs).where(jobs.c.id == job_id)).first())


def list_jobs(repo: Optional[str] = None, active: Optional[bool] = None, status: Optional[str] = None,
              limit: int = 20) -> List[dict]:
    query = select(jobs)
    if repo:
        query = query.where(jobs.c.repo == repo)
    if active is True:
        query = query.where(jobs.c.status.in_(ACTIVE))
    if status:
        query = query.where(jobs.c.status == status)
    with db.engine().connect() as conn:
        return [_as_dict(r) for r in conn.execute(query.order_by(jobs.c.id.desc()).limit(max(1, min(limit, 200))))]


# ---------------------------------------------------------------- consuming

def _sweep(conn, now):
    # leases that ran out on their last attempt: the worker died for good
    conn.execute(update(jobs).where(
        jobs.c.status == "running", jobs.c.lease_until < now, jobs.c.attempts >= jobs.c.max_attempts
    ).values(status="failed", finished_at=now, error="the worker stopped responding (lease expired)"))
    conn.execute(delete(jobs).where(
        jobs.c.status.in_(("done", "failed")), jobs.c.finished_at < now - timedelta(days=KEEP_FINISHED_DAYS)))


def claim(worker: str, lease_seconds: int = LEASE_SECONDS) -> Optional[dict]:
    """Take the oldest runnable job (queued and due, or running with an expired lease). -> job with payload"""
    now = db.utcnow()
    with db.engine().begin() as conn:
        _sweep(conn, now)
        candidate = select(jobs.c.id).where(
            or_(and_(jobs.c.status == "queued", jobs.c.run_after <= now),
                and_(jobs.c.status == "running", jobs.c.lease_until < now)),
            jobs.c.attempts < jobs.c.max_attempts,
        ).order_by(jobs.c.id).limit(1)
        if conn.dialect.name == "postgresql":
            candidate = candidate.with_for_update(skip_locked=True)
        row = conn.execute(
            update(jobs).where(jobs.c.id == candidate.scalar_subquery())
            .values(status="running", attempts=jobs.c.attempts + 1, started_at=now, error=None,
                    lease_until=now + timedelta(seconds=lease_seconds), worker=worker)
            .returning(*jobs.c)
        ).first()
    if row is None:
        return None
    job = _as_dict(row)
    job["payload"] = row.payload
    return job


def report_progress(job_id: int, done: int, total: int, lease_seconds: int = LEASE_SECONDS):
    """Progress counters; also renews the lease."""
    with db.engine().begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job_id, jobs.c.status == "running").values(
            progress_done=done, progress_total=total, lease_until=db.utcnow() + timedelta(seconds=lease_seconds)))


def complete(job_id: int, result: Optional[dict] = None):
    with db.engine().begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job_id).values(
            status="done", result=result, finished_at=db.utcnow(), lease_until=None, error=None))


def fail(job_id: int, error: str):
    """Retry later with exponential backoff, or give up after max_attempts."""
    now = db.utcnow()
    with db.engine().begin() as conn:
        job = conn.execute(select(jobs.c.attempts, jobs.c.max_attempts).where(jobs.c.id == job_id)).first()
        if job is None:
            return
        values = {"error": error[-4000:], "lease_until": None}
        if job.attempts < job.max_attempts:
            values.update(status="queued", run_after=now + timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (job.attempts - 1)))
        else:
            values.update(status="failed", finished_at=now)
        conn.execute(update(jobs).where(jobs.c.id == job_id).values(**values))


def run(job: dict) -> bool:
    """Run one claimed job to completion or failure. -> True when it succeeded."""
    fn = _handlers.get(job["kind"])
    if fn is None:
        fail(job["id"], f"no handler for job kind {job['kind']!r}")
        return False

    last = [0.0]

    def progress(done: int, total: int):
        now = time.monotonic()
        if done >= total or now - last[0] >= 1.0:
            last[0] = now
            report_progress(job["id"], done, total)

    try:
        result = fn(job["payload"], progress)
    except Exception as e:
        logger.exception("job %s (%s) failed", job["id"], job["kind"])
        fail(job["id"], f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
        return False
    complete(job["id"], result)
    return True


def run_pending(worker: str = "inline", limit: Optional[int] = None) -> int:
    """Run due jobs in this thread until none is left (tests, scripts). -> jobs run"""
    count = 0
    while limit is None or count < limit:
        job = claim(worker)
        if job is None:
            break
        run(job)
        count += 1
    return count


# ---------------------------------------------------------------- worker threads

class Worker(threading.Thread):
    def __init__(self, number: int, poll_seconds: float = 1.0):
        super().__init__(name=f"mergeclear-worker-{number}", daemon=True)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{number}"
        self.poll_seconds = poll_seconds
        self.stopping = threading.Event()

    def run(self):
        logger.info("worker %s started", self.worker_id)
        while not self.stopping.is_set():
            try:
                job = claim(self.worker_id)
            except Exception:
                logger.exception("worker %s could not claim a job", self.worker_id)
                job = None
            if job is None:
                self.stopping.wait(self.poll_seconds)
                continue
            run(job)
        logger.info("worker %s stopped", self.worker_id)


_workers: List[Worker] = []


def start_workers(count: int):
    for n in range(count):
        worker = Worker(n + 1)
        worker.start()
        _workers.append(worker)


def stop_workers(timeout: float = 10.0):
    for worker in _workers:
        worker.stopping.set()
    for worker in _workers:
        worker.join(timeout)
    _workers.clear()


# ---------------------------------------------------------------- job kinds

@handler("analyze")
def _analyze(payload: dict, progress) -> dict:
    """Document an uploaded scan report: architecture model, then every changed endpoint."""
    from app.models.schema import AnalyzeRequest
    from app.services.architecture_store import save_architecture
    from app.services.doc_service import process_routes

    request = AnalyzeRequest(**payload)
    if request.architecture:
        save_architecture(request.repository, request.commit, request.architecture)
    progress(0, len(request.routes))
    counts = process_routes(request.routes, request.commit, request.repository, progress=progress)
    return {**counts, "endpoints": len(request.routes)}

"""Job queue and concurrency-safe versioning, on SQLite or (MERGECLEAR_TEST_DATABASE_URL) Postgres."""
import threading
from datetime import timedelta

from sqlalchemy import update

from app import db, jobs
from app.services import docs_store


def _job(payload=None, **kw):
    job, created = jobs.enqueue("test", payload or {"n": 1}, repo="shop", commit="abc", **kw)
    assert created
    return job


def test_identical_uploads_are_queued_once():
    first, created = jobs.enqueue("test", {"n": 1}, repo="shop")
    again, created_again = jobs.enqueue("test", {"n": 1}, repo="shop")
    other, created_other = jobs.enqueue("test", {"n": 2}, repo="shop")
    assert created and not created_again and created_other
    assert again["id"] == first["id"] and other["id"] != first["id"]


def test_jobs_run_and_record_progress_and_result():
    seen = []

    @jobs.handler("test")
    def work(payload, progress):
        for i in range(1, 4):
            progress(i, 3)
        seen.append(payload["n"])
        return {"ok": True}

    job = _job()
    assert jobs.run_pending() == 1
    done = jobs.get(job["id"])
    assert seen == [1]
    assert (done["status"], done["attempts"], done["result"]) == ("done", 1, {"ok": True})
    assert (done["progress_done"], done["progress_total"]) == (3, 3)
    assert jobs.run_pending() == 0


def test_failures_are_retried_with_backoff_then_marked_failed():
    calls = []

    @jobs.handler("test")
    def flaky(payload, progress):
        calls.append(1)
        raise RuntimeError("LLM timed out")

    job = _job(max_attempts=2)
    assert jobs.run_pending() == 1
    retry = jobs.get(job["id"])
    assert retry["status"] == "queued" and "LLM timed out" in retry["error"]
    assert jobs.run_pending() == 0                 # backoff: not due yet

    _make_due(job["id"])
    assert jobs.run_pending() == 1
    failed = jobs.get(job["id"])
    assert (failed["status"], failed["attempts"], len(calls)) == ("failed", 2, 2)


def test_a_job_of_a_dead_worker_is_picked_up_again():
    @jobs.handler("test")
    def work(payload, progress):
        return {"ok": True}

    job = _job()
    claimed = jobs.claim("worker-that-dies")
    assert claimed["id"] == job["id"]
    assert jobs.claim("another") is None           # leased

    _expire_lease(job["id"])
    again = jobs.claim("another")
    assert again["id"] == job["id"] and again["attempts"] == 2
    jobs.run(again)
    assert jobs.get(job["id"])["status"] == "done"


def test_concurrent_workers_never_take_the_same_job():
    @jobs.handler("test")
    def work(payload, progress):
        return None

    ids = [_job({"n": i})["id"] for i in range(12)]
    taken, lock = [], threading.Lock()

    def worker(name):
        while True:
            job = jobs.claim(name)
            if job is None:
                return
            with lock:
                taken.append(job["id"])
            jobs.run(job)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(taken) == sorted(ids)            # each exactly once
    assert {jobs.get(i)["status"] for i in ids} == {"done"}


def test_concurrent_uploads_of_one_api_get_consecutive_versions():
    results, barrier = [], threading.Barrier(6)

    def save(n):
        barrier.wait()
        results.append(docs_store.save_version_if_changed("shop", "Orders.create", f"sig-{n}", f"c{n}", f"doc {n}"))

    threads = [threading.Thread(target=save, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [1, 2, 3, 4, 5, 6]
    assert docs_store.list_versions("shop", "Orders.create") == [1, 2, 3, 4, 5, 6]
    # the same contract again is not a new version
    assert docs_store.save_version_if_changed("shop", "Orders.create", docs_store.latest_signature("shop", "Orders.create"), "c", "x") is None


def _make_due(job_id):
    with db.engine().begin() as conn:
        conn.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(run_after=db.utcnow() - timedelta(seconds=1)))


def _expire_lease(job_id):
    with db.engine().begin() as conn:
        conn.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(lease_until=db.utcnow() - timedelta(seconds=1)))

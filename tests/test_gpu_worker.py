"""Part B — Remote media/GPU seam.

Narrow resumable adapter contract for a future remote Wan2GP worker.
Deterministic fake only: no installation, no live call, no network.

Proves: capability discovery, idempotent submission, status, artifacts,
retry, timeout, cancellation, resume after restart, operator-visible state.
"""
from __future__ import annotations

import time

import pytest

from octopus import gpu_worker, tasks
from octopus.gpu_worker import (
    GpuWorkerError,
    FakeGpuWorker,
    GpuJobStore,
    submit_job,
    poll_status,
    cancel_job,
    job_snapshot,
)

B = "gpu_worker_test"


@pytest.fixture
def store(tmp_path):
    return GpuJobStore(root=tmp_path / "gpu_jobs")


@pytest.fixture
def fake():
    return FakeGpuWorker(name="fake-wan2gp", capabilities=["text2video", "image2video"],
                         max_duration_s=30.0, max_resolution="1080p")


def test_capability_discovery(fake):
    caps = fake.capabilities()
    assert caps["name"] == "fake-wan2gp"
    assert "text2video" in caps["capabilities"]
    assert "image2video" in caps["capabilities"]
    assert caps["max_duration_s"] == 30.0
    assert caps["max_resolution"] == "1080p"


def test_idempotent_submission(store, fake):
    r1 = submit_job(store, fake, B, job_id="job-001", prompt="hello", task_type="text2video")
    assert r1["status"] == "submitted" and r1["remote_id"]
    r2 = submit_job(store, fake, B, job_id="job-001", prompt="hello", task_type="text2video")
    assert r2["duplicate"] is True
    assert r2["remote_id"] == r1["remote_id"]


def test_status_progression(store):
    f = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                      max_resolution="720p", steps=3)
    submit_job(store, f, B, job_id="job-002", prompt="test", task_type="text2video")
    s = poll_status(store, f, B, job_id="job-002")
    for _ in range(5):
        if s["status"] == "completed":
            break
        s = poll_status(store, f, B, job_id="job-002")
    assert s["status"] == "completed"
    assert s["artifacts"]
    assert s["artifacts"][0]["name"] == "output.mp4"


def test_cancellation(store):
    f = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                      max_resolution="720p", steps=100)
    submit_job(store, f, B, job_id="job-003", prompt="slow", task_type="text2video")
    cancel_job(store, f, B, job_id="job-003")
    s = poll_status(store, f, B, job_id="job-003")
    assert s["status"] == "cancelled"
    cancel_job(store, f, B, job_id="job-003")  # idempotent
    s2 = poll_status(store, f, B, job_id="job-003")
    assert s2["status"] == "cancelled"


def test_timeout(store):
    f = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                      max_resolution="720p", steps=999)
    submit_job(store, f, B, job_id="job-004", prompt="timeout", task_type="text2video",
               timeout_s=0.01)
    time.sleep(0.05)
    s = poll_status(store, f, B, job_id="job-004")
    assert s["status"] == "timeout"


def test_resume_after_restart(tmp_path):
    store_dir = tmp_path / "gpu_resume"
    store_dir.mkdir(parents=True, exist_ok=True)
    store1 = GpuJobStore(root=store_dir)
    fake1 = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                          max_resolution="720p", steps=5)
    submit_job(store1, fake1, B, job_id="job-005", prompt="resume", task_type="text2video")
    poll_status(store1, fake1, B, job_id="job-005")
    # "Restart": new store + new adapter, same disk
    store2 = GpuJobStore(root=store_dir)
    fake2 = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                          max_resolution="720p", steps=5)
    r = submit_job(store2, fake2, B, job_id="job-005", prompt="resume", task_type="text2video")
    assert r["duplicate"] is True
    s = poll_status(store2, fake2, B, job_id="job-005")
    assert s["status"] in ("running", "completed", "cancelled", "timeout")


def test_retry_after_failure(store):
    f = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                      max_resolution="720p", steps=2, fail_first_n=1)
    submit_job(store, f, B, job_id="job-006", prompt="retry", task_type="text2video")
    s = poll_status(store, f, B, job_id="job-006")
    if s["status"] == "failed":
        r = submit_job(store, f, B, job_id="job-006", prompt="retry", task_type="text2video",
                       retry=True)
        assert r["status"] == "submitted"
        s2 = poll_status(store, f, B, job_id="job-006")
        for _ in range(5):
            if s2["status"] == "completed":
                break
            s2 = poll_status(store, f, B, job_id="job-006")
        assert s2["status"] == "completed"
    else:
        assert s["status"] in ("running", "completed")


def test_operator_snapshot(store):
    f = FakeGpuWorker(name="fake", capabilities=["text2video"], max_duration_s=10.0,
                      max_resolution="720p", steps=2)
    submit_job(store, f, B, job_id="job-007", prompt="snap", task_type="text2video")
    poll_status(store, f, B, job_id="job-007")
    snap = job_snapshot(store, B, job_id="job-007")
    assert snap["job_id"] == "job-007"
    assert snap["status"] in ("running", "completed")
    assert "history" in snap and len(snap["history"]) >= 1
    assert snap["remote_id"]


def test_invalid_capability_rejected(store):
    f = FakeGpuWorker(name="limited", capabilities=["text2video"],
                      max_duration_s=10.0, max_resolution="720p")
    with pytest.raises(GpuWorkerError, match="capability"):
        submit_job(store, f, B, job_id="job-008", prompt="x", task_type="image2video")


def test_events_journaled(store, fake):
    submit_job(store, fake, B, job_id="job-009", prompt="events", task_type="text2video")
    poll_status(store, fake, B, job_id="job-009")
    events = tasks.events(limit=50)
    gpu_events = [e for e in events if e["type"].startswith("gpu_job.")]
    assert any(e["type"] == "gpu_job.submitted" for e in gpu_events)
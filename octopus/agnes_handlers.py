"""Worker handlers for Agnes video production.

These handlers implement the full cycle that autonomous missions can delegate:
- request generation (preserving task ID immediately)
- poll with backoff
- retrieve MP4
- verify physically
- persist location, SHA-256 and evidence
- continue mission without manual intervention (except human boundaries)

Agnes remains optional: unavailability never blocks other OCTOPUS activities.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import agnes, agnes_production, strategy
from .strategy import StrategyError
from .worker import handler


def _business_from_ctx(ctx) -> str:
    return getattr(ctx, "business", None) or (ctx.task.get("business") if hasattr(ctx, "task") else None) or "octopus"


@handler("agnes.generate_video", max_attempts=3, retry_delay_s=120)
def generate_video(ctx):
    """Full Agnes production cycle as a durable worker task.

    Input:
    {
        "prompt": "Une mer calme au coucher du soleil",
        "idempotency_key": "mission-123-video",  # required for crash-resume & idempotence
        "objective_id": 1,  # optional, for evidence linking
        "experiment_id": 2,  # optional
        "base_url": "http://127.0.0.1:8765",  # optional, defaults to loopback
        "poll_timeout_s": 1800  # optional
    }

    Output:
    {
        "generation_id": int,
        "agnes_task_id": str,
        "status": "done",
        "output_path": str,
        "sha256": str,
        "bytes": int,
        "verified": True
    }

    Crash-resume: if worker crashes after submission, same idempotency_key will
    resume tracking existing Agnes task, not create duplicate.
    """
    inp = ctx.input or {}
    prompt = str(inp.get("prompt") or "").strip()
    idem = str(inp.get("idempotency_key") or "").strip()
    if not prompt:
        raise ValueError("prompt required")
    if not idem:
        raise ValueError("idempotency_key required for durable Agnes generation")

    objective_id = inp.get("objective_id")
    experiment_id = inp.get("experiment_id")
    base_url = str(inp.get("base_url") or agnes.DEFAULT_URL)
    poll_timeout = float(inp.get("poll_timeout_s", agnes.POLL_TIMEOUT_S))

    business = _business_from_ctx(ctx)

    # Memoized submission: if task replays after human answer or retry, do not re-submit
    def do_submit():
        return agnes_production.request_generation(
            business=business,
            prompt=prompt,
            idempotency_key=idem,
            task_id=ctx.id,
            objective_id=int(objective_id) if objective_id is not None else None,
            experiment_id=int(experiment_id) if experiment_id is not None else None,
            base_url=base_url,
        )

    gen = ctx.memo(f"agnes_submit:{idem}", do_submit)
    gen_id = gen["id"] if isinstance(gen, dict) else int(gen)

    # Track with backoff — this is also memoized in steps to survive crashes?
    # We poll live, but we save progress in DB; on crash, next attempt will resume via idempotency.

    # For long polling, we need to respect cancellation
    def do_track():
        # This may take minutes; check cancel periodically via heartbeat already in worker
        # We implement polling loop that checks ctx.cancelled()
        agnes_task_id = agnes_production.get_generation(gen_id)["agnes_task_id"]
        deadline = time.time() + poll_timeout
        interval = agnes.POLL_INITIAL_S
        attempts = 0
        last_status = None
        while time.time() < deadline:
            if ctx.cancelled():
                # Request stop if cancelled
                try:
                    agnes_production.stop_generation(gen_id, base_url=base_url)
                except Exception:
                    pass
                raise ctx._cancel  # will be caught as TaskCancelled
            attempts += 1
            try:
                cur = agnes.status(agnes_task_id, base_url=base_url)
                last_status = cur["status"]
                if last_status in ("completed", "failed"):
                    return agnes_production.get_generation(gen_id)
                # Update DB progress
                agnes_production.update_generation(gen_id, status=last_status, phase=last_status,
                                                   progress=min(95, attempts * 5), attempts=attempts)
            except agnes.AgnesRateLimited:
                interval = min(agnes.POLL_MAX_S, interval * 1.5 + 5)
                time.sleep(interval)
                continue
            except agnes.AgnesUnavailable:
                interval = min(agnes.POLL_MAX_S, interval * 1.2)
                time.sleep(interval)
                continue
            except StrategyError as exc:
                # If status says failed, mark failed
                if "failed" in str(exc).lower():
                    agnes_production.update_generation(gen_id, status="failed", error=str(exc)[:500])
                    raise
                time.sleep(interval)
                interval = min(agnes.POLL_MAX_S, interval * 1.25)
                continue
            time.sleep(interval)
            interval = min(agnes.POLL_MAX_S, interval * 1.25)
        raise StrategyError(f"Agnes polling timeout, last_status={last_status}")

    # Only poll if not already done
    current = agnes_production.get_generation(gen_id)
    if current["status"] not in ("done", "completed"):
        # Use memo for tracking? No, tracking should be live, not memoized, because status changes
        # But we can use a non-memoized call; crash will resume via DB
        try:
            tracked = do_track()
            # Now tracked should be completed or failed
            if tracked["status"] == "failed":
                raise StrategyError(f"Agnes generation failed: {tracked.get('error')}")
            # Update to completed if needed
            if tracked["status"] != "done":
                agnes_production.update_generation(gen_id, status="completed", phase="completed", progress=100)
        except Exception as exc:
            # If rate limited, we want retry, not final failure
            if isinstance(exc, agnes.AgnesRateLimited):
                # Mark for retry
                raise ValueError(f"rate limited, retry: {exc}") from exc
            raise
    else:
        # Already completed/done, ensure file still valid
        pass

    # Retrieve and verify — memoized because download is expensive and file is persisted
    def do_retrieve():
        return agnes_production.retrieve_and_verify(gen_id, base_url=base_url, task_id=ctx.id)

    final = ctx.memo(f"agnes_retrieve:{idem}", do_retrieve)

    ctx.emit("agnes.video.done", {
        "generation_id": final["id"],
        "agnes_task_id": final["agnes_task_id"],
        "path": final["output_path"],
        "sha256": final["sha256"],
        "bytes": final["file_size"],
        "verified": True,
    })

    return {
        "generation_id": final["id"],
        "agnes_task_id": final["agnes_task_id"],
        "status": final["status"],
        "output_path": final["output_path"],
        "sha256": final["sha256"],
        "bytes": final["file_size"],
        "verified": True,
        "evidence_id": final.get("evidence_id"),
    }


@handler("agnes.stop_video", max_attempts=2, retry_delay_s=30)
def stop_video(ctx):
    """Stop a running Agnes generation.

    Input: {"generation_id": int, "base_url": optional}
    """
    inp = ctx.input or {}
    gen_id = inp.get("generation_id")
    if gen_id is None:
        raise ValueError("generation_id required")
    base_url = str(inp.get("base_url") or agnes.DEFAULT_URL)
    result = agnes_production.stop_generation(int(gen_id), base_url=base_url)
    ctx.emit("agnes.video.stopped", {"generation_id": result["id"], "agnes_task_id": result["agnes_task_id"]})
    return {"generation_id": result["id"], "status": result["status"]}


@handler("agnes.status", max_attempts=2, retry_delay_s=10)
def status_handler(ctx):
    """Check Agnes service health and optionally a task status.

    Input: {"task_id": optional 12-hex, "base_url": optional}
    """
    inp = ctx.input or {}
    base_url = str(inp.get("base_url") or agnes.DEFAULT_URL)
    health = agnes.probe(base_url)
    out = {"health": health}
    task_id = inp.get("task_id")
    if task_id:
        try:
            st = agnes.status_full(str(task_id), base_url=base_url)
            out["task"] = st
        except Exception as exc:
            out["task_error"] = str(exc)[:500]
    ctx.emit("agnes.status", out)
    return out


@handler("agnes.download_video", max_attempts=3, retry_delay_s=60)
def download_video(ctx):
    """Download and verify an already completed Agnes video.

    Input: {"generation_id": int, "base_url": optional}
    """
    inp = ctx.input or {}
    gen_id = inp.get("generation_id")
    if gen_id is None:
        raise ValueError("generation_id required")
    base_url = str(inp.get("base_url") or agnes.DEFAULT_URL)
    result = agnes_production.retrieve_and_verify(int(gen_id), base_url=base_url, task_id=ctx.id)
    ctx.emit("agnes.video.downloaded", {"generation_id": result["id"], "path": result["output_path"],
                                        "sha256": result["sha256"]})
    return {"generation_id": result["id"], "path": result["output_path"],
            "sha256": result["sha256"], "bytes": result["file_size"], "verified": True}

"""Full production lifecycle for Agnes video generation.

Implements the cycle:
Objective -> Supervisor -> Mission -> Request Agnes -> Track progress
-> Retrieve MP4 -> Verify deliverable -> Persist proofs -> Continue mission.

Ownership:
- OCTOPUS owns economic decision, task/experiment/evidence refs, permission, cost/time bookkeeping, verification.
- Agnes owns video workflow, retries, media pipeline.

This module does NOT reimplement Agnes internals. It uses the narrow HTTP boundary
in octopus.agnes and persists proofs durably.

Key guarantees:
- Task ID preserved immediately after submission (via task_steps and DB)
- Status polling with backoff (no excessive calls)
- Crash-resume: same idempotency_key returns same Agnes task, no duplicate generation
- Stop request supported
- MP4 verified physically (header, size, SHA-256), not just HTTP 200
- Location, SHA-256 and evidence persisted
- Uncertain submission never triggers blind new generation
- Free quota but with quantity limits, duration caps, 429 handling, cost observation
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import agnes, journal, strategy, tasks
from .strategy import StrategyError

# --- schema for agnes generations (separate from legacy media_generations) ---

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agnes_video_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    business TEXT NOT NULL,
    task_id INTEGER,
    objective_id INTEGER,
    experiment_id INTEGER,
    agnes_task_id TEXT,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    phase TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    output_path TEXT,
    sha256 TEXT,
    file_size INTEGER,
    duration_s REAL,
    source_ref TEXT,
    evidence_id INTEGER,
    idempotency_key TEXT UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agnes_business ON agnes_video_generations(business, created_at);
CREATE INDEX IF NOT EXISTS idx_agnes_agnes_id ON agnes_video_generations(agnes_task_id);
CREATE INDEX IF NOT EXISTS idx_agnes_task ON agnes_video_generations(task_id);
CREATE INDEX IF NOT EXISTS idx_agnes_objective ON agnes_video_generations(objective_id);
"""

_DAILY_LIMIT = agnes.MAX_DAILY_GENERATIONS
_MAX_PROMPT = agnes.MAX_PROMPT_LENGTH
_VIDEO_DIR_ENV = "OCTOPUS_AGNES_VIDEO_DIR"


def _video_root() -> Path:
    env = __import__("os").environ.get(_VIDEO_DIR_ENV, "").strip()
    if env:
        return Path(env)
    from . import paths
    return paths.home() / "data" / "agnes_videos"


def ensure_schema() -> None:
    conn = journal.connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _now() -> float:
    return time.time()


def _check_daily_limit(business: str) -> None:
    """Enforce quantity limit even though upstream is free."""
    since = _now() - 86400
    rows = journal.query(
        "SELECT COUNT(*) AS n FROM agnes_video_generations WHERE business=? AND created_at>=? AND status NOT IN ('failed')",
        (business, since),
    )
    count = rows[0]["n"] if rows else 0
    if count >= _DAILY_LIMIT:
        raise StrategyError(f"Agnes daily limit reached ({_DAILY_LIMIT} per 24h for {business})")


def _find_channel(business: str, *, capability: str = "agnes_submit") -> dict | None:
    """Find active human-authorized Agnes channel with given capability."""
    from . import economy
    try:
        chans = economy.channels(business, status="active", capability=capability)
    except Exception:
        return None
    for ch in chans:
        if ch.get("kind") == "agnes_video" and ch.get("access") == "act":
            # Ensure capability list contains requested one (channels() already filters, but double-check)
            caps = ch.get("capabilities", [])
            if isinstance(caps, str):
                try:
                    caps = json.loads(caps)
                except Exception:
                    caps = []
            if capability in (caps or []):
                return ch
    return None


def _dest_path(business: str, agnes_task_id: str, task_id: int | None) -> Path:
    safe_biz = "".join(c if c.isalnum() or c in "-_" else "_" for c in business)[:64] or "octopus"
    if task_id is not None:
        return _video_root() / safe_biz / f"t{task_id}" / f"{agnes_task_id}.mp4"
    return _video_root() / safe_biz / f"{agnes_task_id}.mp4"


def create_generation(*, business: str, prompt: str, idempotency_key: str,
                      task_id: int | None = None, objective_id: int | None = None,
                      experiment_id: int | None = None, agnes_task_id: str | None = None,
                      status: str = "queued") -> int:
    ensure_schema()
    now = _now()
    with tasks._tx() as conn:
        # Idempotency: return existing if same key
        existing = conn.execute(
            "SELECT id FROM agnes_video_generations WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute(
            "INSERT INTO agnes_video_generations (created_at, updated_at, business, task_id, objective_id, "
            "experiment_id, agnes_task_id, prompt, status, idempotency_key, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (now, now, business, task_id, objective_id, experiment_id,
             agnes_task_id, prompt[:_MAX_PROMPT], status, idempotency_key),
        )
        gen_id = int(cur.lastrowid)
        tasks._emit(conn, business, task_id, "agnes.generation.created",
                    {"id": gen_id, "agnes_task_id": agnes_task_id, "prompt": prompt[:120]})
        return gen_id


def update_generation(gen_id: int, **fields) -> None:
    ensure_schema()
    allowed = {"agnes_task_id", "status", "phase", "progress", "error", "output_path",
               "sha256", "file_size", "duration_s", "source_ref", "evidence_id", "attempts"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {unknown}")
    if not fields:
        return
    fields["updated_at"] = _now()
    with tasks._tx() as conn:
        conn.execute(
            f"UPDATE agnes_video_generations SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
            [*fields.values(), gen_id],
        )
        # Emit event for observability
        row = conn.execute("SELECT business, task_id FROM agnes_video_generations WHERE id=?", (gen_id,)).fetchone()
        if row:
            tasks._emit(conn, row["business"], row["task_id"], "agnes.generation.updated",
                        {"id": gen_id, **{k: v for k, v in fields.items() if k != "updated_at"}})


def get_generation(gen_id: int) -> dict | None:
    ensure_schema()
    rows = journal.query("SELECT * FROM agnes_video_generations WHERE id=?", (gen_id,))
    return dict(rows[0]) if rows else None


def get_by_idempotency(idempotency_key: str) -> dict | None:
    ensure_schema()
    rows = journal.query("SELECT * FROM agnes_video_generations WHERE idempotency_key=?", (idempotency_key,))
    return dict(rows[0]) if rows else None


def get_by_agnes_id(agnes_task_id: str) -> dict | None:
    ensure_schema()
    rows = journal.query("SELECT * FROM agnes_video_generations WHERE agnes_task_id=? ORDER BY id DESC LIMIT 1",
                         (agnes_task_id,))
    return dict(rows[0]) if rows else None


def list_generations(business: str, *, limit: int = 50) -> list[dict]:
    ensure_schema()
    rows = journal.query(
        "SELECT * FROM agnes_video_generations WHERE business=? ORDER BY id DESC LIMIT ?",
        (business, limit),
    )
    return [dict(r) for r in rows]


def request_generation(*, business: str, prompt: str, idempotency_key: str,
                       task_id: int | None = None, objective_id: int | None = None,
                       experiment_id: int | None = None, base_url: str = agnes.DEFAULT_URL) -> dict:
    """Request a video generation, preserving Agnes task ID immediately.

    Uses economic channel + actions.propose if available, otherwise direct probe
    with human boundary. Enforces daily limit and prompt length.

    Returns generation record.
    Never triggers blind new generation if previous submission is ambiguous.
    """
    ensure_schema()
    business = strategy._business(business)
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= _MAX_PROMPT:
        raise StrategyError(f"prompt must be 1..{_MAX_PROMPT} chars")
    prompt = prompt.strip()

    # Idempotency first: if same key exists, return it, do not re-submit
    existing = get_by_idempotency(idempotency_key)
    if existing:
        # If it already has agnes_task_id, we can resume tracking
        return existing

    _check_daily_limit(business)

    # Check for ambiguous actions with same idempotency key in channel_actions
    # If ambiguous exists, do not blindly create new generation — ask to reconcile
    amb = journal.query(
        "SELECT id, status FROM channel_actions WHERE idempotency_key=? AND status='ambiguous'",
        (idempotency_key,),
    )
    if amb:
        raise StrategyError(
            f"Previous submission with key {idempotency_key!r} is ambiguous (action #{amb[0]['id']}); "
            "reconcile with Agnes service before new generation"
        )

    # Try to find authorized channel
    channel = _find_channel(business)
    agnes_task_id = None
    source_ref = None

    if channel is not None:
        if agnes._base_url(base_url) != agnes._base_url(channel.get("locator") or ""):
            raise StrategyError("Agnes URL does not match the active human-authorized channel")
        # Use actions.propose path (durable, with evidence, handles crash)
        from . import actions
        # For free_quota, spend not required, but we still support optional allowance
        result = actions.propose(
            business, int(channel["id"]), "submit", {"prompt": prompt},
            requested_by=f"task:{task_id}" if task_id else "agnes_production",
            idempotency_key=idempotency_key,
            experiment_id=experiment_id,
        )
        if result["status"] == "blocked":
            raise StrategyError(f"Agnes submission blocked: {result.get('reason')}")
        if result["status"] == "ambiguous":
            # Preserve reservation, do not retry blindly
            raise StrategyError(
                f"Agnes submission ambiguous (action #{result['action_id']}): {result.get('reason')}; "
                "reconcile before retry"
            )
        if result["status"] == "failed":
            reason = (result.get("reason") or "").lower()
            if "rate limited" in reason or "429" in reason or "rate_limited" in reason:
                # Explicit rate limit: allow controlled resume with backoff, no extra generation, not ambiguous
                raise agnes.AgnesRateLimited(f"Agnes submission rate limited (429): {result.get('reason')}")
            raise StrategyError(f"Agnes submission failed: {result.get('reason')}")
        # Executed — extract task_id from result evidence
        # The action result is stored in channel_actions.result
        act_rows = journal.query("SELECT result FROM channel_actions WHERE id=?", (result["action_id"],))
        if act_rows and act_rows[0]["result"]:
            try:
                res = json.loads(act_rows[0]["result"])
                agnes_task_id = res.get("task_id")
                source_ref = res.get("source_ref")
            except Exception:
                pass
        if not agnes_task_id:
            # Fallback: try to get from evidence
            ev_rows = journal.query("SELECT source_ref FROM strategy_evidence WHERE id=?", (result.get("evidence_id"),))
            if ev_rows:
                # source_ref is like http://127.0.0.1:8765/api/tasks/<id>
                ref = ev_rows[0]["source_ref"]
                # Extract last part
                import re
                m = re.search(r"/api/tasks/([0-9a-f]{12})", ref or "")
                if m:
                    agnes_task_id = m.group(1)
                    source_ref = ref
    else:
        raise StrategyError(
            f"No active human-authorized Agnes channel (kind=agnes_video, capability=agnes_submit) for {business}; "
            "human must grant act access"
        )

    if not agnes_task_id:
        raise StrategyError("Agnes submission did not return task ID")

    # Persist generation immediately
    gen_id = create_generation(
        business=business, prompt=prompt, idempotency_key=idempotency_key,
        task_id=task_id, objective_id=objective_id, experiment_id=experiment_id,
        agnes_task_id=agnes_task_id, status="queued",
    )
    # Update with source_ref if we have it
    if source_ref:
        update_generation(gen_id, agnes_task_id=agnes_task_id, source_ref=source_ref, status="queued")

    # Also save in task_steps for crash-resume if task_id provided
    if task_id is not None:
        try:
            tasks.save_step(task_id, f"agnes.{idempotency_key}", {"agnes_task_id": agnes_task_id, "gen_id": gen_id})
        except Exception:
            pass

    return get_generation(gen_id)


def track_generation(gen_id: int, *, base_url: str = agnes.DEFAULT_URL,
                     poll_timeout_s: float = agnes.POLL_TIMEOUT_S) -> dict:
    """Track progress with backoff, without excessive calls. Resume-safe."""
    ensure_schema()
    gen = get_generation(gen_id)
    if not gen:
        raise StrategyError(f"Generation #{gen_id} not found")
    agnes_task_id = gen["agnes_task_id"]
    if not agnes_task_id:
        raise StrategyError(f"Generation #{gen_id} has no Agnes task ID")

    # If already done/failed, return
    if gen["status"] in ("done", "failed", "stopped"):
        return gen

    # Poll with backoff
    try:
        result = agnes.poll_until_done(agnes_task_id, base_url=base_url, timeout_s=poll_timeout_s)
        new_status = result["status"]
        if new_status == "completed":
            update_generation(gen_id, status="completed", phase="completed", progress=100,
                              source_ref=result["source_ref"], attempts=result["attempts"])
        else:  # failed
            update_generation(gen_id, status="failed", phase="failed", error="Agnes reported failed",
                              attempts=result["attempts"])
    except agnes.AgnesRateLimited as exc:
        # Rate limited — keep queued, will retry later
        update_generation(gen_id, status="queued", phase="rate_limited", error=str(exc)[:500])
        raise
    except StrategyError as exc:
        # Polling timeout or other — keep last known, but record error
        update_generation(gen_id, error=str(exc)[:500])
        raise

    return get_generation(gen_id)


def stop_generation(gen_id: int, *, base_url: str = agnes.DEFAULT_URL) -> dict:
    """Request stop of a running Agnes task. Must go through actions.propose with human-authorized channel."""
    ensure_schema()
    gen = get_generation(gen_id)
    if not gen:
        raise StrategyError(f"Generation #{gen_id} not found")
    agnes_task_id = gen["agnes_task_id"]
    if not agnes_task_id:
        raise StrategyError("No Agnes task ID to stop")

    # Find channel for stop capability — no direct HTTP bypass
    channel = _find_channel(gen["business"], capability="agnes_stop")
    if channel is None:
        raise StrategyError(
            f"No active human-authorized Agnes channel (kind=agnes_video, capability=agnes_stop) for {gen['business']}; "
            "human must grant act access with agnes_stop capability"
        )

    from . import actions
    idem_key = f"agnes-stop-{agnes_task_id}"
    result = actions.propose(
        gen["business"], int(channel["id"]), "stop", {"task_id": agnes_task_id},
        requested_by="agnes_production", idempotency_key=idem_key,
    )
    if result["status"] in ("blocked", "failed"):
        raise StrategyError(f"Stop blocked/failed: {result.get('reason')}")
    if result["status"] == "ambiguous":
        raise StrategyError(f"Stop ambiguous, reconcile: {result.get('reason')}")
    update_generation(gen_id, status="stopped", phase="stopped")
    return get_generation(gen_id)


def retrieve_and_verify(gen_id: int, *, base_url: str = agnes.DEFAULT_URL,
                        task_id: int | None = None) -> dict:
    """Retrieve MP4, verify physically, record location, SHA-256 and evidence.

    Returns generation with verified=True only after real file check.
    HTTP completed alone is NOT proof.
    """
    ensure_schema()
    gen = get_generation(gen_id)
    if not gen:
        raise StrategyError(f"Generation #{gen_id} not found")
    agnes_task_id = gen["agnes_task_id"]
    if not agnes_task_id:
        raise StrategyError("No Agnes task ID")

    # Ensure task completed in Agnes
    try:
        cur = agnes.status(agnes_task_id, base_url=base_url)
    except Exception as exc:
        raise StrategyError(f"Cannot get Agnes status: {exc}") from None
    if cur["status"] != "completed":
        raise StrategyError(f"Agnes task not completed (status={cur['status']})")

    # Determine destination
    dest = _dest_path(gen["business"], agnes_task_id, task_id or gen["task_id"])
    # Download and verify
    try:
        dl = agnes.download_video(agnes_task_id, dest, base_url=base_url)
    except Exception as exc:
        update_generation(gen_id, status="failed", error=f"download failed: {exc}"[:500])
        raise

    # Extra verification (already done in download, but double-check)
    ver = agnes.verify_mp4(Path(dl["path"]))
    if not ver.get("verified"):
        update_generation(gen_id, status="failed", error=f"MP4 verification failed: {ver.get('reason')}")
        raise StrategyError(f"MP4 verification failed: {ver.get('reason')}")

    # Persist proof
    update_generation(
        gen_id,
        status="done",
        phase="verified",
        output_path=dl["path"],
        sha256=dl["sha256"],
        file_size=dl["bytes"],
        source_ref=dl["source_ref"],
    )

    # Create strategy evidence for the deliverable
    try:
        evidence_id = strategy.create(
            "evidence",
            gen["business"],
            f"Vidéo Agnes vérifiée #{gen_id} (task {agnes_task_id})",
            created_by="agnes_production",
            nature="observed",
            source_type="file",
            source_ref=dl["path"],
            captured_at=time.time(),
            observation=json.dumps({
                "agnes_task_id": agnes_task_id,
                "path": dl["path"],
                "sha256": dl["sha256"],
                "bytes": dl["bytes"],
                "prompt": gen["prompt"][:200],
                "verified": True,
            }, ensure_ascii=False),
            experiment_id=gen["experiment_id"],
            channel_id=None,
        )
        update_generation(gen_id, evidence_id=evidence_id)
        # Link to objective if present
        if gen["objective_id"]:
            try:
                strategy.link(gen["business"], "evidence", evidence_id, "objective", gen["objective_id"], "measures")
            except Exception:
                pass
    except Exception:
        # Evidence creation failure should not invalidate verified file, but log
        pass

    return get_generation(gen_id)


def resume_from_task_step(task_id: int, idempotency_key: str) -> dict | None:
    """Resume tracking after crash using task_steps.

    Returns generation if found, else None.
    """
    ensure_schema()
    try:
        data = tasks.step_value(task_id, f"agnes.{idempotency_key}", None)
    except Exception:
        data = None
    if not data or not isinstance(data, dict):
        return None
    agnes_task_id = data.get("agnes_task_id")
    gen_id = data.get("gen_id")
    if gen_id:
        gen = get_generation(int(gen_id))
        if gen:
            return gen
    if agnes_task_id:
        gen = get_by_agnes_id(agnes_task_id)
        if gen:
            return gen
    return None


def full_production_cycle(*, business: str, prompt: str, idempotency_key: str,
                          task_id: int | None = None, objective_id: int | None = None,
                          experiment_id: int | None = None, base_url: str = agnes.DEFAULT_URL,
                          poll_timeout_s: float = agnes.POLL_TIMEOUT_S) -> dict:
    """Full cycle: request, track, retrieve, verify, persist.

    This is the function that OCTOPUS mission should delegate to.
    It handles crash-resume, rate limits, verification.

    Returns final generation record with verified deliverable.
    """
    # Resume if crash
    if task_id is not None:
        resumed = resume_from_task_step(task_id, idempotency_key)
        if resumed:
            gen_id = resumed["id"]
            # If already done, return
            if resumed["status"] == "done" and resumed["sha256"]:
                # Verify file still present and intact
                if resumed["output_path"]:
                    ver = agnes.verify_mp4(Path(resumed["output_path"]))
                    if ver.get("verified") and ver.get("sha256") == resumed["sha256"]:
                        return resumed
                # File missing or changed — need to re-download if Agnes still has it
            # Otherwise continue tracking
        else:
            # No resume, check idempotency in DB
            existing = get_by_idempotency(idempotency_key)
            if existing:
                gen_id = existing["id"]
            else:
                gen_id = None
    else:
        existing = get_by_idempotency(idempotency_key)
        gen_id = existing["id"] if existing else None

    if gen_id is None:
        # New request
        gen = request_generation(
            business=business, prompt=prompt, idempotency_key=idempotency_key,
            task_id=task_id, objective_id=objective_id, experiment_id=experiment_id,
            base_url=base_url,
        )
        gen_id = gen["id"]
    else:
        gen = get_generation(gen_id)

    # If already done and verified, return
    if gen["status"] == "done" and gen["output_path"]:
        ver = agnes.verify_mp4(Path(gen["output_path"]))
        if ver.get("verified"):
            return gen

    # Track
    gen = track_generation(gen_id, base_url=base_url, poll_timeout_s=poll_timeout_s)

    if gen["status"] == "failed":
        raise StrategyError(f"Agnes generation failed: {gen.get('error')}")

    if gen["status"] not in ("completed", "done"):
        # Still not completed — caller should retry later or wait
        # For full cycle, we expect completed; if not, raise
        if gen["status"] == "completed":
            pass
        else:
            raise StrategyError(f"Agnes generation not completed yet: {gen['status']}")

    # Retrieve and verify
    gen = retrieve_and_verify(gen_id, base_url=base_url, task_id=task_id)

    return gen

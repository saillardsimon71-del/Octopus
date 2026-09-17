"""Bibliothèque des générations : une ligne par vidéo demandée, un dossier par génération.

Fichiers : data/media/video/<id>/ (job.json, events.jsonl, preview.jpg, bridge.log, <titre>.mp4).
La base garde le prompt, les réglages, l'état, la progression et les métadonnées du fichier final,
ce qui permet l'historique, la réutilisation des prompts et les variantes (parent_id).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

from .. import journal, paths

COLUMNS_UPDATABLE = {"status", "phase", "progress", "status_text", "preview_path", "output_path", "duration_s",
                     "width", "height", "fps", "file_size", "generation_seconds", "error", "task_id", "settings", "tags"}


def media_root() -> Path:
    d = paths.data_dir() / "media" / "video"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generation_dir(gen_id: int) -> Path:
    d = media_root() / f"{gen_id:06d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(text: str, limit: int = 48) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", ascii_text))[:limit].strip("-") or "video"


def _decode(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["settings"] = json.loads(item.get("settings") or "{}")
    item["tags"] = json.loads(item["tags"]) if item.get("tags") else []
    return item


def create(business: str, provider: str, model_type: str, prompt: str, settings: dict, *, task_id: int | None = None,
           parent_id: int | None = None, batch_key: str | None = None, variant: int = 0,
           tags: list[str] | None = None) -> int:
    now = time.time()
    conn = journal.connect()
    try:
        cur = conn.execute(
            "INSERT INTO media_generations (created_at, updated_at, business, task_id, parent_id, batch_key, variant, "
            "provider, model_type, prompt, settings, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, now, business, task_id, parent_id, batch_key, variant, provider, model_type, prompt,
             json.dumps(settings, ensure_ascii=False, default=str), json.dumps(tags or [], ensure_ascii=False)))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update(gen_id: int, **fields) -> None:
    unknown = set(fields) - COLUMNS_UPDATABLE
    if unknown:
        raise ValueError(f"colonnes inconnues : {sorted(unknown)}")
    if "settings" in fields:
        fields["settings"] = json.dumps(fields["settings"], ensure_ascii=False, default=str)
    if "tags" in fields:
        fields["tags"] = json.dumps(fields["tags"], ensure_ascii=False)
    fields["updated_at"] = time.time()
    conn = journal.connect()
    try:
        conn.execute(f"UPDATE media_generations SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     (*fields.values(), gen_id))
        conn.commit()
    finally:
        conn.close()


def get(gen_id: int) -> dict | None:
    rows = journal.query("SELECT * FROM media_generations WHERE id=?", (gen_id,))
    return _decode(rows[0]) if rows else None


def by_task(task_id: int) -> list[dict]:
    return [_decode(r) for r in journal.query("SELECT * FROM media_generations WHERE task_id=? ORDER BY variant, id",
                                              (task_id,))]


def recent(limit: int = 50, *, status: str | None = None, business: str | None = None, search: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM media_generations WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if business:
        sql += " AND business=?"
        params.append(business)
    if search:
        sql += " AND prompt LIKE ?"
        params.append(f"%{search}%")
    rows = journal.query(sql + " ORDER BY id DESC LIMIT ?", tuple(params + [limit]))
    return [_decode(r) for r in rows]


def probe_media(path: Path) -> dict:
    """Durée, résolution et images/s mesurées par ffprobe (vide si ffprobe est absent)."""
    if not shutil.which("ffprobe") or not path.exists():
        return {"file_size": path.stat().st_size} if path.exists() else {}
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60).stdout
        data = json.loads(out or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"file_size": path.stat().st_size}
    stream = (data.get("streams") or [{}])[0]
    fps = None
    if "/" in str(stream.get("r_frame_rate", "")):
        num, den = stream["r_frame_rate"].split("/")
        fps = round(float(num) / float(den), 3) if float(den) else None
    duration = (data.get("format") or {}).get("duration")
    return {"width": stream.get("width"), "height": stream.get("height"), "fps": fps,
            "duration_s": round(float(duration), 3) if duration else None, "file_size": path.stat().st_size}

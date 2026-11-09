from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .config import settings
from .db import now, row, transaction
from .discs import playlist_stream
from .media import MediaError, inspect_source
from .mux import mux_command, validate_output
from .previews import preview_once
from .process import run_process
from .util import ensure_beneath, json_load


_JOB_UPDATE_LOCK = threading.Lock()
_JOB_UPDATE_STATE: dict[int, dict[str, Any]] = {}


def _claim_source() -> dict[str, Any] | None:
    with transaction(immediate=True) as connection:
        candidate = connection.execute(
            """
            SELECT * FROM sources
             WHERE present=1 AND probe_status='inspection_queued'
             ORDER BY updated_at
             LIMIT 1
            """
        ).fetchone()
        if candidate is None:
            return None
        connection.execute(
            "UPDATE sources SET probe_status='inspecting', updated_at=? WHERE id=?",
            (now(), candidate["id"]),
        )
        return dict(candidate)


def probe_once() -> bool:
    source = _claim_source()
    if not source:
        return False
    try:
        path = Path(source["path"])
        stat = path.stat()
        if time.time() - stat.st_mtime < settings.stability_seconds:
            raise MediaError(
                "This source changed recently and may still be downloading. "
                "Wait briefly, then inspect it again."
            )
        size = stat.st_size if path.is_file() else source["size"]
        mtime = stat.st_mtime
        metadata = inspect_source(path, source["kind"])
        with transaction() as connection:
            connection.execute(
                """
                UPDATE sources
                   SET size=?, mtime=?, probe_status='ready', probe_error=NULL,
                       metadata_json=?, updated_at=?
                 WHERE id=? AND present=1 AND probe_status='inspecting' AND mtime=?
                """,
                (size, mtime, json.dumps(metadata), now(), source["id"], source["mtime"]),
            )
    except Exception as error:
        with transaction() as connection:
            connection.execute(
                """
                UPDATE sources
                   SET probe_status='error', probe_error=?, updated_at=?
                 WHERE id=? AND present=1 AND probe_status='inspecting' AND mtime=?
                """,
                (str(error)[-3000:], now(), source["id"], source["mtime"]),
            )
    return True


def _claim_job() -> dict[str, Any] | None:
    with transaction(immediate=True) as connection:
        candidate = connection.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if candidate is None:
            return None
        connection.execute(
            """
            UPDATE jobs
               SET status='running', started_at=?, updated_at=?,
                   attempts=attempts+1, cancel_requested=0, error=NULL
             WHERE id=?
            """,
            (now(), now(), candidate["id"]),
        )
        return dict(candidate)


def _job_cancelled(job_id: int) -> bool:
    current = row("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))
    return bool(current and current["cancel_requested"])


def _job_update(
    job_id: int,
    *,
    progress: float | None = None,
    phase: str | None = None,
    line: str | None = None,
    force: bool = False,
) -> None:
    monotonic = time.monotonic()
    with _JOB_UPDATE_LOCK:
        state = _JOB_UPDATE_STATE.setdefault(
            job_id,
            {"lines": [], "last_flush": 0.0, "last_progress": None},
        )
        if line:
            state["lines"].append(line)
        progress_bucket = int(progress) if progress is not None else None
        should_flush = (
            force
            or phase is not None
            or not line
            or len(state["lines"]) >= 20
            or monotonic - float(state["last_flush"]) >= 2.0
            or (
                progress_bucket is not None
                and progress_bucket != state["last_progress"]
                and monotonic - float(state["last_flush"]) >= 0.4
            )
        )
        if not should_flush:
            return
        lines = state["lines"]
        state["lines"] = []
        state["last_flush"] = monotonic
        if progress_bucket is not None:
            state["last_progress"] = progress_bucket

    log_chunk = "\n".join(lines)
    with transaction() as connection:
        if log_chunk:
            connection.execute(
                """
                UPDATE jobs
                   SET log=substr(log || ? || char(10), -100000),
                       progress=COALESCE(?, progress),
                       phase=COALESCE(?, phase), updated_at=?
                 WHERE id=?
                """,
                (log_chunk, progress, phase, now(), job_id),
            )
        else:
            connection.execute(
                """
                UPDATE jobs
                   SET progress=COALESCE(?, progress),
                       phase=COALESCE(?, phase), updated_at=?
                 WHERE id=?
                """,
                (progress, phase, now(), job_id),
            )


def _flush_job_update(job_id: int) -> None:
    _job_update(job_id, force=True)


def _run_job(job: dict[str, Any]) -> None:
    source = row("SELECT * FROM sources WHERE id=?", (job["source_id"],))
    if not source or not source["present"]:
        raise MediaError("Source no longer exists")
    snapshot = json_load(job.get("input_json"), {})
    path = Path(source["path"])
    if snapshot:
        stat = path.stat()
        if stat.st_mtime != snapshot["mtime"] or (path.is_file() and stat.st_size != snapshot["size"]):
            raise MediaError("Source changed since this job was reviewed. Inspect it again.")
        title = snapshot["title"]
    else:
        # Older file jobs used MKVToolNix track IDs held in the source catalog.
        metadata = json_load(source["metadata_json"], {})
        title = next((t for t in metadata.get("titles", []) if t["id"] == job["title_id"]), None)
        if source["kind"] != "file" or not title:
            raise MediaError("Inspect the source and choose its title again")

    output = Path(job["output_path"])
    output_root = (
        settings.movies_dir
        if job["output_kind"] == "movie"
        else settings.television_dir
    )
    ensure_beneath(output, output_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not job["overwrite_approved"]:
        with transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                   SET status='awaiting_overwrite', phase='Destination exists',
                       updated_at=?
                 WHERE id=?
                """,
                (now(), job["id"]),
            )
        return

    partial = output.with_name(f".{output.name}.job-{job['id']}.partial")
    partial.unlink(missing_ok=True)
    selected_keys = json_load(job["selected_tracks_json"], [])
    overrides = json_load(job.get("track_overrides_json"), {})

    def cancelled() -> bool:
        return _job_cancelled(job["id"])

    duration = title.get("duration") or 0

    def report(progress: float | None, line: str) -> None:
        if duration and line.startswith("out_time_us="):
            try:
                progress = min(98, max(0, float(line.split("=", 1)[1]) / 10000 / duration))
            except ValueError:
                pass
        _job_update(job["id"], progress=progress, line=line)

    chapters = settings.work_dir / f"chapters-{job['id']}.ffmeta"
    try:
        command, success_codes, selected = mux_command(path, title, selected_keys, overrides, partial, chapters)
        _job_update(job["id"], progress=0, phase="Muxing selected tracks")
        with playlist_stream(path, title, check_end=True) as stream:
            code = run_process(command, report, cancelled, stdin=stream)
            _flush_job_update(job["id"])
            if cancelled():
                raise InterruptedError("Cancelled")
            if code not in success_codes:
                raise MediaError(f"{Path(command[0]).name} failed. See the job log for the reader error.")
        if cancelled():
            raise InterruptedError("Cancelled")
        _job_update(job["id"], progress=99, phase="Verifying output")
        validate_output(partial, selected, title)
        with transaction(immediate=True) as connection:
            state = connection.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if state["cancel_requested"]:
                raise InterruptedError("Cancelled")
            if job["overwrite_approved"]:
                os.replace(partial, output)
            else:
                try:
                    os.link(partial, output)
                except FileExistsError:
                    connection.execute("""
                        UPDATE jobs SET status='awaiting_overwrite', phase='Destination exists',
                               updated_at=? WHERE id=?
                    """, (now(), job["id"]))
                    return
            connection.execute(
                """
                UPDATE jobs
                   SET status='complete', progress=100, phase='Complete',
                       error=NULL, finished_at=?, updated_at=?
                 WHERE id=?
                """,
                (now(), now(), job["id"]),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO cleanup
                    (job_id, source_id, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (job["id"], source["id"], now()),
            )
    finally:
        partial.unlink(missing_ok=True)
        chapters.unlink(missing_ok=True)
        with _JOB_UPDATE_LOCK:
            _JOB_UPDATE_STATE.pop(job["id"], None)


def remux_once() -> bool:
    job = _claim_job()
    if not job:
        return False
    try:
        _run_job(job)
    except InterruptedError:
        with transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                   SET status='cancelled', phase='Cancelled', error=NULL,
                       finished_at=?, updated_at=?
                 WHERE id=?
                """,
                (now(), now(), job["id"]),
            )
    except Exception as error:
        with transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                   SET status='failed', phase='Failed', error=?,
                       finished_at=?, updated_at=?
                 WHERE id=?
                """,
                (str(error)[-3000:], now(), now(), job["id"]),
            )
    return True


def _loop(name: str, operation: Any) -> None:
    while True:
        try:
            worked = operation()
        except Exception as error:
            print(f"{name} worker error: {error}", flush=True)
            worked = False
        if not worked:
            time.sleep(settings.poll_seconds)


def _recover_interrupted_work() -> None:
    """Recover claims left behind when the single worker service stopped."""
    with transaction(immediate=True) as connection:
        timestamp = now()
        connection.execute(
            """
            UPDATE jobs
               SET status='retryable',
                   error=COALESCE(error, 'Worker stopped while this job was running'),
                   updated_at=?
             WHERE status='running'
            """,
            (timestamp,),
        )
        connection.execute(
            """
            UPDATE previews SET status='queued', updated_at=?
             WHERE status='running'
            """,
            (timestamp,),
        )
        connection.execute(
            """
            UPDATE sources SET probe_status='inspection_queued', updated_at=?
             WHERE probe_status='inspecting'
            """,
            (timestamp,),
        )


def run_worker() -> None:
    _recover_interrupted_work()
    threads = []
    specs = [
        ("remux", settings.remux_concurrency, remux_once),
        ("probe", settings.probe_concurrency, probe_once),
        ("preview", settings.preview_concurrency, preview_once),
    ]
    for name, count, operation in specs:
        for index in range(count):
            thread = threading.Thread(
                target=_loop,
                args=(f"{name}-{index + 1}", operation),
                daemon=True,
            )
            thread.start()
            threads.append(thread)
    print(
        "Workers started: "
        f"{settings.remux_concurrency} remux, "
        f"{settings.probe_concurrency} probe, "
        f"{settings.preview_concurrency} preview",
        flush=True,
    )
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        print("Worker stopping", flush=True)

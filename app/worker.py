from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings
from .db import connect, now, row, transaction
from .media import (
    MediaError,
    executable,
    inspect_mkvmerge,
    inspect_output,
    inspect_ffprobe,
    inspect_source,
    makemkv_source,
)
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
                 WHERE id=?
                """,
                (size, mtime, json.dumps(metadata), now(), source["id"]),
            )
    except Exception as error:
        with transaction() as connection:
            connection.execute(
                """
                UPDATE sources
                   SET probe_status='error', probe_error=?, updated_at=?
                 WHERE id=?
                """,
                (str(error)[-3000:], now(), source["id"]),
            )
    return True


def _claim_preview() -> dict[str, Any] | None:
    with transaction(immediate=True) as connection:
        candidate = connection.execute(
            "SELECT * FROM previews WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if candidate is None:
            return None
        connection.execute(
            "UPDATE previews SET status='running', updated_at=? WHERE id=?",
            (now(), candidate["id"]),
        )
        return dict(candidate)


def _preview_input(source: dict[str, Any]) -> Path:
    path = Path(source["path"])
    if source["kind"] == "file":
        return path
    if source["kind"] == "iso":
        raise MediaError("Audio preview from ISO images is not currently available")
    suffixes = {".vob"} if source["kind"] == "dvd" else {".m2ts", ".mts"}
    candidates = [
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in suffixes
    ]
    if not candidates:
        raise MediaError("No playable stream file was found in this disc structure")
    return max(candidates, key=lambda item: item.stat().st_size)


def preview_once() -> bool:
    preview = _claim_preview()
    if not preview:
        return False
    source = row("SELECT * FROM sources WHERE id=?", (preview["source_id"],))
    try:
        if not source:
            raise MediaError("Source no longer exists")
        metadata = json_load(source["metadata_json"], {})
        title = next(
            (
                value
                for value in metadata.get("titles", [])
                if str(value["id"]) == str(preview["title_id"])
            ),
            None,
        )
        if not title:
            raise MediaError("Selected title was not found")
        track = next(
            (
                value
                for value in title.get("tracks", [])
                if str(value["key"]) == str(preview["track_key"])
            ),
            None,
        )
        preview_type = preview.get("preview_type") or "audio"
        expected_track_type = "audio" if preview_type == "audio" else "subtitle"
        if not track or track["type"] != expected_track_type:
            raise MediaError(f"Selected {expected_track_type} track was not found")
        source_path = _preview_input(source)
        duration = float(title.get("duration") or 0)
        try:
            preview_metadata = inspect_ffprobe(source_path)
            duration = float(
                preview_metadata["titles"][0].get("duration") or duration
            )
        except Exception:
            pass
        start = max(0, duration / 2 - 30) if duration else 0
        if preview_type == "subtitle":
            output, result = _create_subtitle_preview(
                preview, source_path, track, start
            )
        else:
            output = settings.preview_dir / f"preview-{preview['id']}.mp3"
            command = [
                executable("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(start),
                "-i",
                str(source_path),
                "-map",
                f"0:a:{int(track.get('ordinal', 0))}",
                "-t",
                "60",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                "-y",
                str(output),
            ]
            code = run_process(command, lambda *_: None, lambda: False)
            if code != 0 or not output.exists() or output.stat().st_size == 0:
                raise MediaError("FFmpeg could not create this audio preview")
            result = {"kind": "audio"}
        with transaction() as connection:
            connection.execute(
                """
                UPDATE previews
                   SET status='complete', path=?, result_json=?,
                       error=NULL, updated_at=?
                 WHERE id=?
                """,
                (
                    str(output) if output else None,
                    json.dumps(result),
                    now(),
                    preview["id"],
                ),
            )
    except Exception as error:
        with transaction() as connection:
            connection.execute(
                "UPDATE previews SET status='failed', error=?, updated_at=? WHERE id=?",
                (str(error)[-2000:], now(), preview["id"]),
            )
    return True


SRT_BLOCK = re.compile(
    r"(?:^|\n)\s*\d+\s*\n"
    r"(?P<time>\d{2}:\d{2}:\d{2}[,\.]\d{3}\s+-->\s+"
    r"\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"
    r"(?P<text>.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)


def _create_subtitle_preview(
    preview: dict[str, Any],
    source_path: Path,
    track: dict[str, Any],
    start: float,
) -> tuple[Path | None, dict[str, Any]]:
    if source_path.suffix.lower() == ".iso":
        raise MediaError("Subtitle preview from ISO images is unavailable")
    srt_output = settings.preview_dir / f"preview-{preview['id']}.srt"
    srt_command = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0, start)),
        "-i",
        str(source_path),
        "-map",
        f"0:s:{int(track.get('ordinal', 0))}",
        "-t",
        "180",
        "-c:s",
        "srt",
        "-y",
        str(srt_output),
    ]
    code = run_process(srt_command, lambda *_: None, lambda: False)
    if code == 0 and srt_output.exists() and srt_output.stat().st_size:
        text = srt_output.read_text(errors="replace")
        cues = [
            {
                "time": match.group("time"),
                "text": re.sub(r"<[^>]+>", "", match.group("text")).strip(),
            }
            for match in SRT_BLOCK.finditer(text)
        ]
        if cues:
            midpoint = len(cues) // 2
            snippets = cues[max(0, midpoint - 1) : midpoint + 2]
            srt_output.unlink(missing_ok=True)
            return None, {"kind": "text", "snippets": snippets}
    srt_output.unlink(missing_ok=True)

    # Bitmap subtitles (PGS/VobSub) cannot be converted to text without OCR.
    # Render a representative frame instead, only after the user requests it.
    image_output = settings.preview_dir / f"preview-{preview['id']}.png"
    image_command = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0, start)),
        "-i",
        str(source_path),
        "-filter_complex",
        f"[0:v:0][0:s:{int(track.get('ordinal', 0))}]overlay",
        "-frames:v",
        "1",
        "-y",
        str(image_output),
    ]
    code = run_process(image_command, lambda *_: None, lambda: False)
    if code == 0 and image_output.exists() and image_output.stat().st_size:
        return image_output, {
            "kind": "image",
            "message": "Image subtitle frame; language must be confirmed visually.",
        }
    image_output.unlink(missing_ok=True)
    raise MediaError(
        "No subtitle cue could be rendered near the middle of this title"
    )


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


def _selected_by_type(
    title: dict[str, Any],
    selected_keys: list[str],
    overrides: dict[str, dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    selected = {str(value) for value in selected_keys}
    grouped = {"video": [], "audio": [], "subtitle": []}
    for track in title.get("tracks", []):
        if str(track["key"]) in selected and track["type"] in grouped:
            selected_track = dict(track)
            override = overrides.get(str(track["key"]), {})
            selected_track["language_override"] = override.get("language", "")
            selected_track["name_override"] = override.get("name", "")
            grouped[track["type"]].append(selected_track)
    return grouped


def _selection_options(grouped: dict[str, list[dict[str, Any]]]) -> list[str]:
    options: list[str] = []
    mapping = {
        "video": ("--no-video", "--video-tracks"),
        "audio": ("--no-audio", "--audio-tracks"),
        "subtitle": ("--no-subtitles", "--subtitle-tracks"),
    }
    for track_type, (none_option, selected_option) in mapping.items():
        tracks = grouped[track_type]
        if tracks:
            options.extend(
                [selected_option, ",".join(str(track["id"]) for track in tracks)]
            )
            for track in tracks:
                if track.get("language_override"):
                    options.extend(
                        [
                            "--language",
                            f"{track['id']}:{track['language_override']}",
                        ]
                    )
                if track.get("name_override"):
                    options.extend(
                        ["--track-name", f"{track['id']}:{track['name_override']}"]
                    )
        else:
            options.append(none_option)
    return options


def _map_disc_tracks(
    temporary_source: Path, grouped: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    inspected = inspect_mkvmerge(temporary_source)
    generated_tracks = inspected["titles"][0]["tracks"]
    result = {"video": [], "audio": [], "subtitle": []}
    for track_type, selected in grouped.items():
        selected_ordinals = {
            int(track.get("ordinal", 0)): track for track in selected
        }
        result[track_type] = [
            {
                **track,
                "language_override": selected_ordinals[
                    int(track.get("ordinal", 0))
                ].get("language_override", ""),
                "name_override": selected_ordinals[
                    int(track.get("ordinal", 0))
                ].get("name_override", ""),
            }
            for track in generated_tracks
            if track["type"] == track_type
            and int(track.get("ordinal", 0)) in selected_ordinals
        ]
        if len(result[track_type]) != len(selected):
            raise MediaError(
                f"MakeMKV output did not contain every selected {track_type} track"
            )
    return result


def _run_job(job: dict[str, Any]) -> None:
    source = row("SELECT * FROM sources WHERE id=?", (job["source_id"],))
    if not source or not source["present"]:
        raise MediaError("Source no longer exists")
    metadata = json_load(source["metadata_json"], {})
    title = next(
        (
            value
            for value in metadata.get("titles", [])
            if str(value["id"]) == str(job["title_id"])
        ),
        None,
    )
    if not title:
        raise MediaError("Selected title is no longer available")

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
    grouped = _selected_by_type(title, selected_keys, overrides)
    if not grouped["video"] and not grouped["audio"]:
        raise MediaError("At least one video or audio track must be selected")

    def cancelled() -> bool:
        return _job_cancelled(job["id"])

    def report(progress: float | None, line: str) -> None:
        _job_update(job["id"], progress=progress, line=line)

    temporary_disc_output: Path | None = None
    mux_input = Path(source["path"])
    try:
        if source["kind"] in {"dvd", "bluray", "iso"}:
            work = settings.work_dir / f"job-{job['id']}"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            _job_update(job["id"], progress=0, phase="Reading disc title")
            command = [
                executable("makemkvcon"),
                "-r",
                "--progress=-same",
                "mkv",
                makemkv_source(Path(source["path"]), source["kind"]),
                str(job["title_id"]),
                str(work),
            ]
            code = run_process(command, report, cancelled)
            _flush_job_update(job["id"])
            if cancelled():
                raise InterruptedError("Cancelled")
            if code != 0:
                raise MediaError("MakeMKV failed; see the job log")
            generated = sorted(work.glob("*.mkv"), key=lambda p: p.stat().st_size)
            if not generated:
                raise MediaError("MakeMKV did not produce an MKV")
            temporary_disc_output = generated[-1]
            mux_input = temporary_disc_output
            grouped = _map_disc_tracks(mux_input, grouped)

        _job_update(job["id"], progress=0, phase="Muxing selected tracks")
        command = [
            executable("mkvmerge"),
            "--output",
            str(partial),
            *_selection_options(grouped),
            str(mux_input),
        ]
        code = run_process(command, report, cancelled)
        _flush_job_update(job["id"])
        if cancelled():
            raise InterruptedError("Cancelled")
        if code not in {0, 1}:
            raise MediaError("MKVToolNix failed; see the job log")
        if not partial.exists() or partial.stat().st_size == 0:
            raise MediaError("No output file was created")

        _job_update(job["id"], progress=99, phase="Validating output")
        validated = inspect_output(partial)
        output_tracks = validated["titles"][0]["tracks"]
        expected_count = sum(len(values) for values in grouped.values())
        if len([t for t in output_tracks if t["type"] != "other"]) < expected_count:
            raise MediaError("Validation found fewer tracks than were selected")
        if output.exists():
            output.unlink()
        os.replace(partial, output)
        with transaction() as connection:
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
        if temporary_disc_output:
            shutil.rmtree(temporary_disc_output.parent, ignore_errors=True)


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

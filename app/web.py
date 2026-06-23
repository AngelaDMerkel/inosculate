from __future__ import annotations

import json
import mimetypes
import os
import shutil
import traceback
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .catalog import LANGUAGES, preferences
from .config import settings
from .db import now, row, rows, transaction
from .scanner import discover
from .util import (
    ensure_beneath,
    format_duration,
    human_bytes,
    json_load,
    safe_component,
    safe_relative_mkv,
)


STATIC_DIR = Path(__file__).parent / "static"


def _public_source(value: dict) -> dict:
    result = dict(value)
    metadata = json_load(result.pop("metadata_json", None), {})
    result["metadata"] = metadata
    result["size_display"] = human_bytes(result.get("size"))
    for title in metadata.get("titles", []):
        title["duration_display"] = format_duration(title.get("duration"))
        title["size_display"] = human_bytes(title.get("size"))
        title["resolution_display"] = (
            f"{title.get('resolution', 'Unknown')} · "
            f"{title.get('resolution_class', 'Unknown')}"
        )
    return result


def _public_job(value: dict) -> dict:
    result = dict(value)
    result["selected_tracks"] = json_load(result.pop("selected_tracks_json"), [])
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = f"Inosculate/{__version__}"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request is too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _send_json(
        self, value: object, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        encoded = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            requested = range_header[6:].split(",", 1)[0]
            start_text, _, end_text = requested.partition("-")
            try:
                if start_text:
                    start = int(start_text)
                    end = int(end_text) if end_text else end
                elif end_text:
                    length = int(end_text)
                    start = max(0, size - length)
            except ValueError:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            if start < 0 or end < start or start >= size:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            end = min(end, size - 1)
        partial = start != 0 or end != size - 1
        self.send_response(
            HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK
        )
        self.send_header(
            "Content-Type",
            content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _route_parts(self) -> list[str]:
        return [part for part in urlparse(self.path).path.split("/") if part]

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def do_GET(self) -> None:
        try:
            parts = self._route_parts()
            if not parts:
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            elif parts[0] == "static" and len(parts) == 2:
                self._send_file(STATIC_DIR / safe_component(parts[1]))
            elif parts == ["api", "health"]:
                self._send_json({"ok": True, "version": __version__})
            elif parts == ["api", "settings"]:
                self._send_json(
                    {
                        "ingest": str(settings.ingest_dir),
                        "movies": str(settings.movies_dir),
                        "television": str(settings.television_dir),
                        "trash_enabled": settings.trash_dir is not None,
                        "languages": [
                            {"code": code, "name": name}
                            for code, name in LANGUAGES
                        ],
                        "concurrency": {
                            "remux": settings.remux_concurrency,
                            "probe": settings.probe_concurrency,
                            "preview": settings.preview_concurrency,
                        },
                    }
                )
            elif parts == ["api", "preferences"]:
                self._send_json(preferences())
            elif parts == ["api", "directories"]:
                query = self._query()
                kind = query.get("kind", [""])[0]
                self._send_json(self._directories(kind))
            elif parts == ["api", "sources"]:
                self._send_json(
                    [
                        _public_source(value)
                        for value in rows(
                            """
                            SELECT sources.*,
                                   (SELECT status FROM jobs
                                     WHERE jobs.source_id=sources.id
                                     ORDER BY jobs.id DESC LIMIT 1)
                                       AS latest_job_status,
                                   (SELECT COUNT(*) FROM jobs
                                     WHERE jobs.source_id=sources.id
                                       AND jobs.status IN
                                           ('queued','running',
                                            'awaiting_overwrite'))
                                       AS active_job_count,
                                   (SELECT COUNT(*) FROM jobs
                                     WHERE jobs.source_id=sources.id
                                       AND jobs.status='complete')
                                       AS completed_job_count
                              FROM sources
                             WHERE present=1
                             ORDER BY COALESCE(series_name, name) COLLATE NOCASE,
                                      season_number, episode_number,
                                      name COLLATE NOCASE
                            """
                        )
                    ]
                )
            elif len(parts) == 3 and parts[:2] == ["api", "sources"]:
                source = row("SELECT * FROM sources WHERE id=?", (int(parts[2]),))
                if not source:
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(_public_source(source))
            elif parts == ["api", "jobs"]:
                self._send_json(
                    [
                        _public_job(value)
                        for value in rows(
                            """
                            SELECT jobs.*, sources.name AS source_name
                              FROM jobs JOIN sources ON sources.id=jobs.source_id
                             ORDER BY jobs.created_at DESC LIMIT 200
                            """
                        )
                    ]
                )
            elif parts == ["api", "cleanup"]:
                self._send_json(
                    rows(
                        """
                        SELECT cleanup.*, sources.name AS source_name,
                               sources.path AS source_path, jobs.output_path
                          FROM cleanup
                          JOIN sources ON sources.id=cleanup.source_id
                          JOIN jobs ON jobs.id=cleanup.job_id
                         WHERE cleanup.status IN ('pending', 'deferred', 'error')
                         ORDER BY cleanup.created_at
                        """
                    )
                )
            elif len(parts) == 3 and parts[:2] == ["api", "previews"]:
                preview = row(
                    "SELECT * FROM previews WHERE id=?", (int(parts[2]),)
                )
                if not preview:
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    value = dict(preview)
                    result = json_load(value.pop("result_json", None), {})
                    value["result"] = result
                    if value["status"] == "complete" and result.get("kind") == "audio":
                        value["audio_url"] = f"/api/previews/{value['id']}/audio"
                    elif (
                        value["status"] == "complete"
                        and result.get("kind") == "image"
                    ):
                        value["image_url"] = f"/api/previews/{value['id']}/asset"
                    value.pop("path", None)
                    self._send_json(value)
            elif (
                len(parts) == 4
                and parts[:2] == ["api", "previews"]
                and parts[3] == "audio"
            ):
                preview = row(
                    "SELECT * FROM previews WHERE id=? AND status='complete'",
                    (int(parts[2]),),
                )
                if not preview:
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    path = ensure_beneath(
                        Path(preview["path"]), settings.preview_dir
                    )
                    self._send_file(path, "audio/mpeg")
            elif (
                len(parts) == 4
                and parts[:2] == ["api", "previews"]
                and parts[3] == "asset"
            ):
                preview = row(
                    "SELECT * FROM previews WHERE id=? AND status='complete'",
                    (int(parts[2]),),
                )
                if not preview or not preview["path"]:
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    path = ensure_beneath(
                        Path(preview["path"]), settings.preview_dir
                    )
                    self._send_file(path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._handle_error(error)

    def do_POST(self) -> None:
        try:
            parts = self._route_parts()
            if parts == ["api", "scan"]:
                self._send_json(discover())
            elif parts == ["api", "preferences"]:
                self._save_preferences(self._json_body())
            elif (
                len(parts) == 4
                and parts[:2] == ["api", "sources"]
                and parts[3] in {"refresh", "inspect"}
            ):
                with transaction() as connection:
                    connection.execute(
                        """
                        UPDATE sources
                           SET probe_status='inspection_queued',
                               probe_error=NULL, updated_at=?
                         WHERE id=?
                        """,
                        (now(), int(parts[2])),
                    )
                self._send_json({"ok": True}, HTTPStatus.ACCEPTED)
            elif parts == ["api", "jobs"]:
                self._create_job(self._json_body())
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                self._job_action(int(parts[2]), parts[3])
            elif parts == ["api", "previews"]:
                self._create_preview(self._json_body())
            elif len(parts) == 3 and parts[:2] == ["api", "cleanup"]:
                self._cleanup_action(int(parts[2]), self._json_body())
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._handle_error(error)

    def _create_job(self, body: dict) -> None:
        source = row("SELECT * FROM sources WHERE id=?", (int(body["source_id"]),))
        if not source or source["probe_status"] != "ready":
            raise ValueError("This source is not ready")
        output_kind = body.get("output_kind")
        if output_kind not in {"movie", "television"}:
            raise ValueError("Choose Movies or Television")
        relative = safe_relative_mkv(str(body.get("relative_path", "")))
        root = (
            settings.movies_dir if output_kind == "movie" else settings.television_dir
        )
        output = ensure_beneath(root / relative, root)
        overwrite = bool(body.get("overwrite"))
        if output.exists() and not overwrite:
            self._send_json(
                {
                    "error": "Destination already exists",
                    "code": "destination_exists",
                    "output_path": str(output),
                },
                HTTPStatus.CONFLICT,
            )
            return
        metadata = json_load(source["metadata_json"], {})
        title_id = str(body.get("title_id", "0"))
        title = next(
            (
                value
                for value in metadata.get("titles", [])
                if str(value["id"]) == title_id
            ),
            None,
        )
        if not title:
            raise ValueError("Selected title was not found")
        available = {str(track["key"]) for track in title.get("tracks", [])}
        selected = list(
            dict.fromkeys(
                str(value)
                for value in body.get("selected_tracks", [])
                if str(value) in available
            )
        )
        if not selected:
            raise ValueError("Select at least one track")
        selected_types = {
            track["type"]
            for track in title.get("tracks", [])
            if str(track["key"]) in selected
        }
        if not selected_types.intersection({"video", "audio"}):
            raise ValueError("Select at least one video or audio track")
        raw_overrides = body.get("track_overrides", {})
        if not isinstance(raw_overrides, dict):
            raise ValueError("Track overrides must be an object")
        language_codes = {code for code, _ in LANGUAGES}
        overrides: dict[str, dict[str, str]] = {}
        for track_key, value in raw_overrides.items():
            if str(track_key) not in available or not isinstance(value, dict):
                continue
            language = str(value.get("language", "")).strip().lower()
            name = safe_component(str(value.get("name", "")).strip(), "")
            if language and language not in language_codes:
                raise ValueError(f"Unsupported track language: {language}")
            if language or name:
                overrides[str(track_key)] = {
                    "language": language,
                    "name": name,
                }
        timestamp = now()
        with transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO jobs
                    (source_id, title_id, output_kind, relative_path, output_path,
                     selected_tracks_json, track_overrides_json,
                     status, overwrite_approved,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    source["id"],
                    title_id,
                    output_kind,
                    str(relative),
                    str(output),
                    json.dumps(selected),
                    json.dumps(overrides),
                    overwrite,
                    timestamp,
                    timestamp,
                ),
            )
            job_id = cursor.lastrowid
        self._send_json({"id": job_id, "status": "queued"}, HTTPStatus.CREATED)

    def _job_action(self, job_id: int, action: str) -> None:
        job = row("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        with transaction() as connection:
            if action == "cancel" and job["status"] in {"queued", "running"}:
                if job["status"] == "queued":
                    connection.execute(
                        """
                        UPDATE jobs SET status='cancelled', phase='Cancelled',
                               finished_at=?, updated_at=? WHERE id=?
                        """,
                        (now(), now(), job_id),
                    )
                else:
                    connection.execute(
                        "UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                        (now(), job_id),
                    )
            elif action == "retry" and job["status"] in {
                "failed",
                "cancelled",
                "retryable",
            }:
                connection.execute(
                    """
                    UPDATE jobs
                       SET status='queued', progress=0, phase=NULL, error=NULL,
                           log='',
                           cancel_requested=0, started_at=NULL, finished_at=NULL,
                           updated_at=?
                     WHERE id=?
                    """,
                    (now(), job_id),
                )
            elif action == "overwrite" and job["status"] == "awaiting_overwrite":
                connection.execute(
                    """
                    UPDATE jobs
                       SET status='queued', overwrite_approved=1, progress=0,
                           phase=NULL, updated_at=?
                     WHERE id=?
                    """,
                    (now(), job_id),
                )
            else:
                raise ValueError(f"Action {action!r} is not valid for this job")
        self._send_json({"ok": True}, HTTPStatus.ACCEPTED)

    def _create_preview(self, body: dict) -> None:
        source_id = int(body["source_id"])
        title_id = str(body.get("title_id", "0"))
        track_key = str(body["track_key"])
        preview_type = str(body.get("preview_type", "audio"))
        if preview_type not in {"audio", "subtitle"}:
            raise ValueError("Unsupported preview type")
        existing = row(
            """
            SELECT * FROM previews
             WHERE source_id=? AND title_id=? AND track_key=?
               AND preview_type=?
               AND status IN ('queued', 'running', 'complete')
             ORDER BY id DESC LIMIT 1
            """,
            (source_id, title_id, track_key, preview_type),
        )
        if existing:
            self._send_json({"id": existing["id"], "status": existing["status"]})
            return
        timestamp = now()
        with transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO previews
                    (source_id, title_id, track_key, preview_type,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    source_id,
                    title_id,
                    track_key,
                    preview_type,
                    timestamp,
                    timestamp,
                ),
            )
        self._send_json(
            {"id": cursor.lastrowid, "status": "queued"}, HTTPStatus.CREATED
        )

    def _cleanup_action(self, cleanup_id: int, body: dict) -> None:
        item = row(
            """
            SELECT cleanup.*, sources.path, sources.kind, sources.present
              FROM cleanup JOIN sources ON sources.id=cleanup.source_id
             WHERE cleanup.id=?
            """,
            (cleanup_id,),
        )
        if not item:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        decision = body.get("decision")
        if decision == "later":
            with transaction() as connection:
                connection.execute(
                    """
                    UPDATE cleanup SET status='deferred', decision='later',
                           decided_at=? WHERE id=?
                    """,
                    (now(), cleanup_id),
                )
            self._send_json({"ok": True})
            return
        if decision == "keep":
            with transaction() as connection:
                connection.execute(
                    """
                    UPDATE cleanup SET status='complete', decision='keep',
                           decided_at=? WHERE id=?
                    """,
                    (now(), cleanup_id),
                )
            self._send_json({"ok": True})
            return
        if decision != "delete" or body.get("confirm") is not True:
            raise ValueError("Deletion requires explicit confirmation")

        source_path = ensure_beneath(Path(item["path"]), settings.ingest_dir)
        try:
            active_job = row(
                """
                SELECT id FROM jobs
                 WHERE source_id=? AND status NOT IN ('complete', 'failed', 'cancelled')
                 LIMIT 1
                """,
                (item["source_id"],),
            )
            if active_job:
                raise ValueError(
                    "This source still has an active or retryable remux job"
                )
            if settings.trash_dir:
                settings.trash_dir.mkdir(parents=True, exist_ok=True)
                destination = settings.trash_dir / (
                    f"{source_path.name}.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
                )
                counter = 1
                while destination.exists():
                    destination = settings.trash_dir / (
                        f"{source_path.name}.{counter}."
                        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
                    )
                    counter += 1
                shutil.move(str(source_path), str(destination))
                final_decision = "trashed"
            elif source_path.is_dir():
                shutil.rmtree(source_path)
                final_decision = "deleted"
            else:
                source_path.unlink()
                final_decision = "deleted"
            with transaction() as connection:
                connection.execute(
                    """
                    UPDATE cleanup SET status='complete', decision=?,
                           error=NULL, decided_at=? WHERE id=?
                    """,
                    (final_decision, now(), cleanup_id),
                )
                connection.execute(
                    "UPDATE sources SET present=0, updated_at=? WHERE id=?",
                    (now(), item["source_id"]),
                )
                connection.execute(
                    """
                    UPDATE cleanup
                       SET status='complete', decision='source_removed',
                           decided_at=COALESCE(decided_at, ?)
                     WHERE source_id=? AND id<>?
                       AND status IN ('pending', 'deferred', 'error')
                    """,
                    (now(), item["source_id"], cleanup_id),
                )
            self._send_json({"ok": True, "decision": final_decision})
        except Exception as error:
            with transaction() as connection:
                connection.execute(
                    "UPDATE cleanup SET status='error', error=? WHERE id=?",
                    (str(error)[-2000:], cleanup_id),
                )
            raise

    def _directories(self, kind: str) -> dict:
        if kind == "movie":
            root = settings.movies_dir
        elif kind == "television":
            root = settings.television_dir
        else:
            raise ValueError("Directory kind must be movie or television")
        if not root.exists():
            raise FileNotFoundError(f"Output root does not exist: {root}")
        directories = [""]
        errors: list[str] = []
        for current, child_directories, _ in os.walk(
            root,
            onerror=lambda error: errors.append(
                f"{error.filename or error}: {error.strerror or 'unreadable'}"
            ),
        ):
            current_path = Path(current)
            relative = current_path.relative_to(root)
            if len(relative.parts) >= 5:
                child_directories[:] = []
                continue
            child_directories[:] = [
                name for name in child_directories if not name.startswith(".")
            ]
            for name in child_directories:
                directories.append(str(relative / name))
                if len(directories) >= 2500:
                    child_directories[:] = []
                    break
            if len(directories) >= 2500:
                break
        directories = sorted(
            set(directories),
            key=lambda value: (value.count(os.sep), value.casefold()),
        )
        return {
            "kind": kind,
            "root": str(root),
            "directories": directories,
            "truncated": len(directories) >= 2500,
            "warnings": errors[:5],
        }

    def _save_preferences(self, body: dict) -> None:
        language_codes = {code for code, _ in LANGUAGES}
        audio = str(body.get("preferred_audio_language", "")).lower()
        subtitle = str(body.get("preferred_subtitle_language", "")).lower()
        if audio and audio not in language_codes:
            raise ValueError("Unsupported preferred audio language")
        if subtitle and subtitle not in language_codes:
            raise ValueError("Unsupported preferred subtitle language")
        ignored_patterns = body.get("ignored_patterns", [])
        if isinstance(ignored_patterns, str):
            ignored_patterns = [
                value.strip()
                for value in ignored_patterns.splitlines()
                if value.strip()
            ]
        if not isinstance(ignored_patterns, list):
            raise ValueError("Ignored patterns must be a list")
        value = {
            "preferred_audio_language": audio,
            "preferred_subtitle_language": subtitle,
            "forced_subtitles_only": bool(body.get("forced_subtitles_only")),
            "ignored_patterns": [
                str(item).strip()[:160]
                for item in ignored_patterns[:100]
                if str(item).strip()
            ],
        }
        with transaction() as connection:
            connection.execute(
                """
                INSERT INTO preferences (key, value_json, updated_at)
                VALUES ('application', ?, ?)
                ON CONFLICT(key) DO UPDATE
                    SET value_json=excluded.value_json,
                        updated_at=excluded.updated_at
                """,
                (json.dumps(value), now()),
            )
        self._send_json(value)

    def _handle_error(self, error: Exception) -> None:
        traceback.print_exc()
        status = (
            HTTPStatus.BAD_REQUEST
            if isinstance(
                error,
                (ValueError, KeyError, json.JSONDecodeError, FileNotFoundError),
            )
            else HTTPStatus.INTERNAL_SERVER_ERROR
        )
        self._send_json({"error": str(error)}, status)


def serve() -> None:
    server = ThreadingHTTPServer((settings.web_host, settings.web_port), Handler)
    print(
        f"Inosculate listening on http://{settings.web_host}:{settings.web_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

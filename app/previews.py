from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .config import settings
from .db import now, row, transaction
from .discs import ffmpeg_input, playlist_path, playlist_stream, stream_args
from .media import MediaError, executable
from .process import run_process
from .util import format_duration, json_load


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


def run_sample(path: Path, title: dict, start: float, output_args: list[str]) -> int:
    with playlist_stream(path, title) as stream:
        seek = ["-ss", str(start)]
        input_args = [*stream_args(), *seek] if stream else [*seek, *ffmpeg_input(path, title["id"])]
        command = [executable("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                   *input_args, *output_args]
        return run_process(command, lambda *_: None, lambda: False, stdin=stream)


def audio_sample(path: Path, title: dict, track: dict, start: float, output: Path) -> None:
    encode = ["-t", "60", "-vn", "-c:a", "libmp3lame", "-q:a", "4", "-y", str(output)]
    if title.get("reader") == "ffmpeg":
        code = run_sample(path, title, start, ["-map", f"0:{track['id']}", *encode])
    else:
        # Use the same track ID and playlist that mkvmerge will use for the job.
        # An FFmpeg audio ordinal can identify a different stream (e.g. a core track).
        source = playlist_path(path, title["id"]) if title["id"].startswith("bd:") else path
        with tempfile.TemporaryDirectory(dir=settings.work_dir) as directory:
            clip = Path(directory) / "sample.mka"
            command = [executable("mkvmerge"), "-o", str(clip)]
            selection = ["--audio-tracks", str(track["id"]), "--no-video", "--no-subtitles",
                         "--no-attachments", "--no-chapters", str(source)]
            diagnostics = []
            code = run_process([*command, "--split",
                                f"parts:{format_duration(start)}-{format_duration(start + 60)}",
                                *selection], lambda _, line: diagnostics.append(line) if "Error:" in line else None,
                               lambda: False)
            offset = 0
            if code not in (0, 1) and any("cannot be split" in line for line in diagnostics):
                # Some codecs (e.g. FLAC) cannot be split by mkvmerge. Isolate
                # only that audio track and seek within it before encoding the sample.
                code = run_process([*command, *selection], lambda *_: None, lambda: False)
                offset = start
            if code not in (0, 1) or not clip.is_file():
                raise MediaError(f"Could not read audio track {track['id']} from {title['name']}")
            code = run_sample(clip, {"id": "0"}, offset, ["-map", "0:a:0", *encode])
    if code != 0 or not output.is_file() or not output.stat().st_size:
        raise MediaError(f"Could not play audio track {track['id']}")


def preview_once() -> bool:
    preview = _claim_preview()
    if not preview:
        return False
    source = row("SELECT * FROM sources WHERE id=?", (preview["source_id"],))
    try:
        if not source or not source["present"]:
            raise MediaError("Source no longer exists")
        if preview.get("source_mtime") is not None and source["mtime"] != preview["source_mtime"]:
            raise MediaError("Source changed. Inspect it again before sampling.")
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
        path = Path(source["path"])
        duration = float(title.get("duration") or 0)
        start = max(0, duration / 2 - 30) if duration else 0
        if preview_type == "subtitle":
            output, result = _create_subtitle_preview(
                preview, path, title, track, start
            )
        else:
            output = settings.preview_dir / f"preview-{preview['id']}.mp3"
            audio_sample(path, title, track, start, output)
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
    path: Path,
    title: dict,
    track: dict[str, Any],
    start: float,
) -> tuple[Path | None, dict[str, Any]]:
    srt_output = settings.preview_dir / f"preview-{preview['id']}.srt"
    code = run_sample(path, title, start, [
        "-map",
        f"0:s:{int(track.get('ordinal', 0))}",
        "-t",
        "180",
        "-c:s",
        "srt",
        "-y",
        str(srt_output),
    ])
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
    code = run_sample(path, title, start, [
        "-filter_complex",
        f"[0:v:0][0:s:{int(track.get('ordinal', 0))}]overlay",
        "-frames:v",
        "1",
        "-y",
        str(image_output),
    ])
    if code == 0 and image_output.exists() and image_output.stat().st_size:
        return image_output, {
            "kind": "image",
            "message": "Image subtitle frame; language must be confirmed visually.",
        }
    image_output.unlink(missing_ok=True)
    raise MediaError(
        "No subtitle cue could be rendered near the middle of this title"
    )

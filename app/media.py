from __future__ import annotations

import csv
import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from .catalog import resolution_class
from .util import parse_duration


class MediaError(RuntimeError):
    pass


def executable(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise MediaError(f"Required executable is unavailable: {name}")
    return found


def run_json(command: list[str], timeout: int = 300) -> dict[str, Any]:
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise MediaError(detail[-2000:] or f"Command failed: {command[0]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MediaError(f"Invalid metadata response from {command[0]}") from error


def _track_type(value: str) -> str:
    lowered = value.lower()
    if lowered in {"video", "audio", "subtitles", "subtitle"}:
        return "subtitle" if lowered.startswith("subtitle") else lowered
    return "other"


def inspect_mkvmerge(path: Path) -> dict[str, Any]:
    payload = run_json([executable("mkvmerge"), "-J", str(path)])
    tracks = []
    type_ordinals: defaultdict[str, int] = defaultdict(int)
    for raw in payload.get("tracks", []):
        properties = raw.get("properties", {})
        track_type = _track_type(raw.get("type", "other"))
        ordinal = type_ordinals[track_type]
        type_ordinals[track_type] += 1
        tracks.append(
            {
                "key": str(raw.get("id")),
                "id": raw.get("id"),
                "ordinal": ordinal,
                "type": track_type,
                "codec": raw.get("codec")
                or properties.get("codec_id")
                or "Unknown",
                "language": properties.get("language_ietf")
                or properties.get("language")
                or "und",
                "name": properties.get("track_name") or "",
                "default": bool(properties.get("default_track")),
                "forced": bool(properties.get("forced_track")),
                "audio_channels": properties.get("audio_channels"),
                "sample_rate": properties.get("audio_sampling_frequency"),
                "width": properties.get("pixel_dimensions", "").split("x")[0]
                if properties.get("pixel_dimensions")
                else None,
                "height": properties.get("pixel_dimensions", "").split("x")[-1]
                if properties.get("pixel_dimensions")
                else None,
            }
        )
    duration_ns = payload.get("container", {}).get("properties", {}).get("duration", 0)
    duration = float(duration_ns or 0) / 1_000_000_000
    return {
        "container": payload.get("container", {}).get("type", "Media"),
        "titles": [
            {
                "id": "0",
                "name": path.stem,
                "duration": duration,
                "size": path.stat().st_size,
                "tracks": tracks,
                "suggested": True,
            }
        ],
    }


def inspect_ffprobe(path: Path) -> dict[str, Any]:
    payload = run_json(
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    tracks = []
    type_ordinals: defaultdict[str, int] = defaultdict(int)
    for stream in payload.get("streams", []):
        track_type = _track_type(stream.get("codec_type", "other"))
        ordinal = type_ordinals[track_type]
        type_ordinals[track_type] += 1
        tags = stream.get("tags", {})
        disposition = stream.get("disposition", {})
        tracks.append(
            {
                "key": str(stream.get("index")),
                "id": stream.get("index"),
                "ordinal": ordinal,
                "type": track_type,
                "codec": stream.get("codec_long_name")
                or stream.get("codec_name")
                or "Unknown",
                "language": tags.get("language", "und"),
                "name": tags.get("title", ""),
                "default": bool(disposition.get("default")),
                "forced": bool(disposition.get("forced")),
                "audio_channels": stream.get("channels"),
                "sample_rate": stream.get("sample_rate"),
                "width": stream.get("width"),
                "height": stream.get("height"),
            }
        )
    duration = parse_duration(payload.get("format", {}).get("duration"))
    return {
        "container": payload.get("format", {}).get("format_long_name", "Media"),
        "titles": [
            {
                "id": "0",
                "name": path.stem,
                "duration": duration,
                "size": path.stat().st_size,
                "tracks": tracks,
                "suggested": True,
            }
        ],
    }


MAKEMKV_CODES = {
    1: "type",
    2: "name",
    3: "language",
    4: "language_name",
    5: "codec_id",
    6: "codec_short",
    7: "codec",
    8: "chapters",
    9: "duration",
    10: "size_text",
    11: "size",
    13: "bitrate",
    14: "audio_channels",
    17: "sample_rate",
    19: "resolution",
    20: "aspect",
    21: "frame_rate",
    25: "output_filename",
}


def makemkv_source(path: Path, kind: str) -> str:
    if kind == "iso":
        return f"iso:{path}"
    return f"file:{path}"


def inspect_makemkv(path: Path, kind: str) -> dict[str, Any]:
    command = [
        executable("makemkvcon"),
        "-r",
        "--cache=1",
        "info",
        makemkv_source(path, kind),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=900, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise MediaError(detail[-3000:] or "MakeMKV could not inspect this source")

    title_values: defaultdict[str, dict[str, Any]] = defaultdict(dict)
    stream_values: defaultdict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        prefix, encoded = line.split(":", 1)
        if prefix not in {"TINFO", "SINFO"}:
            continue
        try:
            values = next(csv.reader([encoded]))
            if prefix == "TINFO" and len(values) >= 4:
                title_id, code, _, value = values[:4]
                title_values[title_id][MAKEMKV_CODES.get(int(code), f"code_{code}")] = value
            elif prefix == "SINFO" and len(values) >= 5:
                title_id, stream_id, code, _, value = values[:5]
                stream_values[(title_id, stream_id)][
                    MAKEMKV_CODES.get(int(code), f"code_{code}")
                ] = value
        except (ValueError, csv.Error):
            continue

    titles = []
    for title_id, info in title_values.items():
        tracks = []
        type_ordinals: defaultdict[str, int] = defaultdict(int)
        for (stream_title, stream_id), stream in stream_values.items():
            if stream_title != title_id:
                continue
            track_type = _track_type(stream.get("type", "other"))
            ordinal = type_ordinals[track_type]
            type_ordinals[track_type] += 1
            resolution = str(stream.get("resolution", ""))
            width, _, height = resolution.partition("x")
            tracks.append(
                {
                    "key": stream_id,
                    "id": stream_id,
                    "ordinal": ordinal,
                    "type": track_type,
                    "codec": stream.get("codec")
                    or stream.get("codec_short")
                    or stream.get("codec_id")
                    or "Unknown",
                    "language": stream.get("language", "und"),
                    "language_name": stream.get("language_name", ""),
                    "name": stream.get("name", ""),
                    "default": False,
                    "forced": "forced" in stream.get("name", "").lower(),
                    "audio_channels": stream.get("audio_channels"),
                    "sample_rate": stream.get("sample_rate"),
                    "width": int(width) if width.isdigit() else None,
                    "height": int(height) if height.isdigit() else None,
                }
            )
        titles.append(
            {
                "id": title_id,
                "name": info.get("name") or f"Title {int(title_id) + 1}",
                "duration": parse_duration(info.get("duration")),
                "size": int(info.get("size", 0) or 0),
                "output_filename": info.get("output_filename"),
                "tracks": tracks,
                "suggested": False,
            }
        )

    if not titles:
        raise MediaError("MakeMKV found no readable titles")
    main = max(titles, key=lambda item: (item["duration"], item["size"]))
    main["suggested"] = True
    titles.sort(key=lambda item: (-item["duration"], item["id"]))
    return {"container": kind.upper(), "titles": titles}


def inspect_source(path: Path, kind: str) -> dict[str, Any]:
    if kind in {"dvd", "bluray", "iso"}:
        return enrich_metadata(inspect_makemkv(path, kind))
    try:
        primary = inspect_mkvmerge(path)
    except (MediaError, subprocess.TimeoutExpired):
        return enrich_metadata(inspect_ffprobe(path))
    # Some containers (notably MP4) do not expose duration or rich track
    # names through mkvmerge identification. Keep mkvmerge's track IDs, which
    # are required for selection, while enriching matching type/ordinal pairs.
    try:
        supplemental = inspect_ffprobe(path)
    except (MediaError, subprocess.TimeoutExpired):
        return enrich_metadata(primary)
    primary_title = primary["titles"][0]
    supplemental_title = supplemental["titles"][0]
    if not primary_title.get("duration"):
        primary_title["duration"] = supplemental_title.get("duration", 0)
    supplemental_tracks = {
        (track["type"], track["ordinal"]): track
        for track in supplemental_title.get("tracks", [])
    }
    for track in primary_title.get("tracks", []):
        extra = supplemental_tracks.get((track["type"], track["ordinal"]))
        if not extra:
            continue
        for key in ("name", "language", "audio_channels", "sample_rate"):
            if not track.get(key) and extra.get(key):
                track[key] = extra[key]
    return enrich_metadata(primary)


def enrich_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    for title in metadata.get("titles", []):
        video = next(
            (
                track
                for track in title.get("tracks", [])
                if track.get("type") == "video"
            ),
            None,
        )
        for track in title.get("tracks", []):
            if track.get("type") == "video":
                width, height = track.get("width"), track.get("height")
                track["resolution"] = (
                    f"{width}×{height}" if width and height else "Unknown"
                )
                track["resolution_class"] = resolution_class(width, height)
        if video:
            title["width"] = video.get("width")
            title["height"] = video.get("height")
            title["resolution"] = video.get("resolution")
            title["resolution_class"] = video.get("resolution_class")
        else:
            title["resolution"] = "Unknown"
            title["resolution_class"] = "Unknown"
    return metadata


def inspect_output(path: Path) -> dict[str, Any]:
    return inspect_mkvmerge(path)

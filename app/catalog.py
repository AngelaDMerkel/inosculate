from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from .db import row


DEFAULT_PREFERENCES: dict[str, Any] = {
    "preferred_audio_language": "",
    "preferred_subtitle_language": "",
    "forced_subtitles_only": False,
    "ignored_patterns": [
        "sample",
        "samples",
        "extras",
        ".inosculate-trash",
    ],
}


LANGUAGES = [
    ("und", "Unknown / undefined"),
    ("eng", "English"),
    ("jpn", "Japanese"),
    ("spa", "Spanish"),
    ("fra", "French"),
    ("deu", "German"),
    ("ita", "Italian"),
    ("por", "Portuguese"),
    ("rus", "Russian"),
    ("zho", "Chinese"),
    ("kor", "Korean"),
    ("ara", "Arabic"),
    ("hin", "Hindi"),
    ("nld", "Dutch"),
    ("pol", "Polish"),
    ("tur", "Turkish"),
    ("swe", "Swedish"),
    ("nor", "Norwegian"),
    ("dan", "Danish"),
    ("fin", "Finnish"),
    ("ces", "Czech"),
    ("hun", "Hungarian"),
    ("ron", "Romanian"),
    ("bul", "Bulgarian"),
    ("ukr", "Ukrainian"),
    ("heb", "Hebrew"),
    ("tha", "Thai"),
    ("vie", "Vietnamese"),
    ("ind", "Indonesian"),
]


def preferences() -> dict[str, Any]:
    stored = row("SELECT value_json FROM preferences WHERE key='application'")
    if not stored:
        return dict(DEFAULT_PREFERENCES)
    try:
        value = json.loads(stored["value_json"])
    except (TypeError, json.JSONDecodeError):
        value = {}
    return {**DEFAULT_PREFERENCES, **value}


def ignored(relative: Path, patterns: list[str]) -> bool:
    lowered = relative.as_posix().lower()
    return any(
        fnmatch.fnmatch(lowered, pattern.lower())
        or any(
            fnmatch.fnmatch(part.lower(), pattern.lower())
            for part in relative.parts
        )
        for pattern in patterns
        if pattern.strip()
    )


EPISODE_PATTERNS = [
    re.compile(
        r"(?i)(?P<series>.*?)[\s._-]+S(?P<season>\d{1,2})E(?P<episode>\d{1,3})"
        r"(?:[\s._-]+(?P<title>.*?))?$"
    ),
    re.compile(
        r"(?i)(?P<series>.*?)[\s._-]+(?P<season>\d{1,2})x"
        r"(?P<episode>\d{1,3})(?:[\s._-]+(?P<title>.*?))?$"
    ),
    re.compile(
        r"(?i)(?P<series>.*?)[\s._-]+Season[\s._-]*(?P<season>\d{1,2})"
        r"[\s._-]+Episode[\s._-]*(?P<episode>\d{1,3})"
        r"(?:[\s._-]+(?P<title>.*?))?$"
    ),
]

RELEASE_NOISE = re.compile(
    r"(?i)\b(?:2160p|1080p|720p|576p|480p|360p|web[- .]?dl|webrip|"
    r"blu[- .]?ray|bdrip|remux|x26[45]|h\.?26[45]|hevc|avc|aac|"
    r"flac|dts(?:-hd)?|proper|repack|dual audio)\b"
)


def _clean_name(value: str) -> str:
    value = re.sub(r"\[[^\]]*]", " ", value)
    value = re.sub(r"\([^)]*(?:1080|720|2160|x26|h26|remux)[^)]*\)", " ", value)
    value = RELEASE_NOISE.sub(" ", value)
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -_.")
    return value


def infer_episode(path: Path, ingest_root: Path) -> dict[str, Any]:
    stem = path.stem if path.is_file() else path.parent.name
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        series = _clean_name(match.group("series"))
        title = _clean_name(match.groupdict().get("title") or "")
        if not series and path.parent != ingest_root:
            series = _clean_name(path.parent.name)
        return {
            "group_key": f"series:{series.casefold()}",
            "series_name": series or "Unknown series",
            "season_number": int(match.group("season")),
            "episode_number": int(match.group("episode")),
            "episode_title": title,
        }

    # Anime and other releases often use a bare episode number but live inside
    # a clearly named series folder. Keep this conservative to avoid years and
    # resolutions being interpreted as episodes.
    if path.parent != ingest_root:
        bare = re.search(
            r"(?i)(?:^|[\s._-])(?P<episode>\d{1,3})(?:v\d+)?"
            r"(?:[\s._-]|$)",
            stem,
        )
        if bare:
            episode = int(bare.group("episode"))
            if episode not in {360, 480, 576, 720}:
                series = _clean_name(path.parent.name)
                return {
                    "group_key": f"series:{series.casefold()}",
                    "series_name": series,
                    "season_number": 1,
                    "episode_number": episode,
                    "episode_title": "",
                }

    return {
        "group_key": None,
        "series_name": None,
        "season_number": None,
        "episode_number": None,
        "episode_title": None,
    }


def resolution_class(width: int | str | None, height: int | str | None) -> str:
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return "Unknown"
    if not w or not h:
        return "Unknown"
    if h >= 1440 or w >= 3000:
        return "2160p"
    if h >= 900:
        return "1080p"
    if h >= 600:
        return "720p"
    if h >= 420:
        return "480p"
    return "360p"


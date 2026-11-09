"""Build a single reader-specific mux command and verify the finished container."""

from collections import Counter
from pathlib import Path
from typing import Any

from .discs import ffmpeg_input, playlist_path, stream_args
from .media import MediaError, executable, inspect_output


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
            selected_track["name_override"] = "" if override.get("clear_name") else override.get("name") or None
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
                if track.get("name_override") is not None:
                    options.extend(
                        ["--track-name", f"{track['id']}:{track['name_override']}"]
                    )
        else:
            options.append(none_option)
    return options


def mux_command(path: Path, title: dict, keys: list[str], overrides: dict, output: Path, chapters: Path):
    grouped = _selected_by_type(title, keys, overrides)
    selected = [track for group in grouped.values() for track in group]
    if len(selected) != len(keys) or not (grouped["video"] or grouped["audio"]):
        raise MediaError("The selected audio or video tracks are unavailable")
    if title.get("reader") != "ffmpeg":
        source = playlist_path(path, title["id"]) if title["id"].startswith("bd:") else path
        command = [executable("mkvmerge"), "--output", str(output)]
        if title.get("chapter_starts"):
            # mkvmerge may drop short final MPLS chapters when it imports them itself.
            lines = []
            for index, ticks in enumerate(title["chapter_starts"], 1):
                minutes, seconds = divmod(ticks // 90000, 60)
                hours, minutes = divmod(minutes, 60)
                nanos = ticks % 90000 * 1000000000 // 90000
                lines.extend([f"CHAPTER{index:02}={hours:02}:{minutes:02}:{seconds:02}.{nanos:09}",
                              f"CHAPTER{index:02}NAME=Chapter {index:02}"])
            chapters.write_text("\n".join(lines) + "\n")
            command.extend(["--chapters", str(chapters), "--no-chapters"])
        command.extend([*_selection_options(grouped), str(source)])
        return command, {0, 1}, selected

    input_args = stream_args() if title.get("stream_playlist") else ffmpeg_input(path, title["id"])
    chapter_input = "0"
    starts = title.get("chapter_starts", [])
    if starts:
        end = round(title["duration"] * 90000)
        entries = [";FFMETADATA1"]
        for start, stop in zip(starts, [*starts[1:], end]):
            if stop > start:
                entries.extend(["[CHAPTER]", "TIMEBASE=1/90000", f"START={start}", f"END={stop}"])
        chapters.write_text("\n".join(entries) + "\n")
        input_args.extend(["-f", "ffmetadata", "-i", str(chapters)])
        chapter_input = "1"
    command = [executable("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "warning",
               "-progress", "pipe:1", *input_args, "-c", "copy"]
    for index, track in enumerate(selected):
        command.extend(["-map", f"0:{track['id']}"])
        # Disc LPCM has disc-specific framing. Store equivalent PCM in Matroska.
        if track.get("codec_id") in {"pcm_dvd", "pcm_bluray"}:
            command.extend([f"-c:{index}", "pcm_s24le"])
        for field, key in [("language", "language_override"), ("title", "name_override")]:
            if track.get(key) or (key == "name_override" and track.get(key) is not None):
                command.extend([f"-metadata:s:{index}", f"{field}={track[key]}"])
    command += ["-map_chapters", chapter_input, "-f", "matroska", "-y", str(output)]
    return command, {0}, selected


def validate_output(path: Path, selected: list[dict], title: dict) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise MediaError("The reader produced an empty output")
    output = inspect_output(path)["titles"][0]
    expected = Counter(t["type"] for t in selected)
    actual = Counter(t["type"] for t in output["tracks"] if t["type"] != "other")
    if expected != actual:
        raise MediaError("The output track counts differ from the reviewed selection")
    if title.get("chapter_starts") and output["chapters"] != len(title["chapter_starts"]):
        raise MediaError("The output chapter count differs from the selected disc title")
    duration = title.get("duration") or 0
    if title["id"] != "0" and expected["video"] and duration and output["duration"] < duration - max(2, duration * 0.015):
        raise MediaError(f"Output ended early ({output['duration']:.1f}s of {duration:.1f}s)")

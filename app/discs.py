"""Catalog logical disc titles first. Open streams only for a chosen title."""

from __future__ import annotations

import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from .media import MediaError, executable, inspect_ffprobe, inspect_mkvmerge, run_json
from .util import parse_duration


def listing(tool: str, *arguments: str) -> str:
    result = subprocess.run(
        [executable(tool), *arguments], capture_output=True, text=True,
        errors="replace", timeout=120,
    )
    if result.returncode:
        raise MediaError((result.stderr or result.stdout).strip()[-1200:] or f"{tool} could not read the disc")
    return result.stdout


def dvd_titles(path: Path) -> list[dict]:
    output = listing("lsdvd", "-Ox", "-c", str(path))
    # libdvdread occasionally prints diagnostics ahead of lsdvd's XML.
    start, end = output.find("<lsdvd>"), output.rfind("</lsdvd>")
    if start < 0 or end < start:
        raise MediaError("No DVD title table was found")
    try:
        xml = output[start:end + len("</lsdvd>")]
        # lsdvd can emit an unescaped ampersand from the volume label or path.
        xml = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[\da-fA-F]+;)", "&amp;", xml)
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise MediaError(f"Invalid DVD title table: {exc}") from exc
    titles = []
    for track in root.findall("track"):
        number = int(track.findtext("ix", "0"))
        duration = parse_duration(track.findtext("length"))
        chapters = track.findall("chapter")
        starts = []
        elapsed = 0
        for chapter in chapters:
            starts.append(elapsed)
            elapsed += round(parse_duration(chapter.findtext("length")) * 90000)
        if number and duration:
            titles.append({"id": f"dvd:{number}", "name": f"Title {number}",
                           "duration": duration, "chapters": len(chapters), "chapter_starts": starts,
                           "reader": "ffmpeg"})
    return titles


def bluray_titles(path: Path) -> list[dict]:
    playlists = run_json([executable("inosculate-bluray-catalog"), str(path)], timeout=120)
    # FFmpeg's bluray protocol requires at least one relevant playlist >= 3 min.
    # Short-only ISOs use libbluray's playlist stream directly.
    short_image = path.is_file() and not any(p["ticks"] >= 180 * 90000 for p in playlists)
    titles = []
    for playlist in playlists:
        number = f"{playlist['playlist']:05d}"
        titles.append({
            "id": f"bd:{number}", "name": f"Playlist {number}",
            "duration": playlist["ticks"] / 90000, "chapters": len(playlist["starts"]),
            "chapter_starts": playlist["starts"],
            "reader": "mkvmerge" if path.is_dir() else "ffmpeg",
            "stream_playlist": short_image,
        })
    return titles


def catalog_disc(path: Path, kind: str) -> dict:
    disc = path.parent if kind in {"dvd", "bluray"} else path
    readers = {"dvd": dvd_titles, "bluray": bluray_titles}
    errors = []
    for format_name in (("bluray", "dvd") if kind == "iso" else (kind,)):
        try:
            titles = readers[format_name](disc)
            if titles:
                return {"container": "Blu-ray" if format_name == "bluray" else "DVD",
                        "disc_catalog": 1, "titles": titles}
            errors.append(f"{format_name}: no readable titles")
        except (MediaError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{format_name}: {exc}")
    raise MediaError("Could not read the disc title table. " + " / ".join(errors))


def ffmpeg_input(path: Path, title_id: str) -> list[str]:
    disc = path.parent if path.is_dir() else path
    prefix, _, number = title_id.partition(":")
    if prefix == "dvd" and number.isdecimal():
        return ["-f", "dvdvideo", "-title", str(int(number)), "-i", str(disc)]
    if prefix == "bd" and number.isdecimal():
        return ["-playlist", str(int(number)), "-probesize", "64M", "-analyzeduration",
                "30000000", "-i", f"bluray:{disc}"]
    if title_id == "0" and path.is_file():
        return ["-i", str(path)]
    raise MediaError("The source must be inspected again to identify this title")


def playlist_path(path: Path, title_id: str) -> Path:
    if not re.fullmatch(r"bd:\d{5}", title_id):
        raise MediaError("Invalid Blu-ray playlist")
    playlist_dir = next((p for p in path.iterdir() if p.name.lower() == "playlist"), None)
    filename = title_id[3:] + ".mpls"
    if playlist_dir:
        for file in playlist_dir.iterdir():
            if file.name.lower() == filename:
                return file
    raise MediaError(f"Playlist {filename} is missing from {path}")


def inspect_title(path: Path, title: dict) -> dict:
    if title["reader"] == "mkvmerge":
        metadata = inspect_mkvmerge(playlist_path(path, title["id"]))
    else:
        with playlist_stream(path, title) as stream:
            args = stream_args() if stream else ffmpeg_input(path, title["id"])
            metadata = inspect_ffprobe(path, args, stream)
    tracks = [t for t in metadata["titles"][0]["tracks"] if t["type"] in {"video", "audio", "subtitle"}]
    if not any(t["type"] in {"video", "audio"} for t in tracks):
        raise MediaError(f"No readable audio or video in {title['name']}")
    return {**title, "tracks": tracks}


def stream_args() -> list[str]:
    return ["-f", "mpegts", "-probesize", "64M", "-analyzeduration", "30000000", "-i", "pipe:0"]


@contextmanager
def playlist_stream(path: Path, title: dict, *, check_end: bool = False):
    if not title.get("stream_playlist"):
        yield None
        return
    number = int(title["id"].split(":")[1])
    process = subprocess.Popen([executable("bd_splice"), "-p", str(number), str(path)],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        yield process.stdout
        if check_end and process.wait(timeout=10) != 0:
            raise MediaError(f"Could not finish reading Blu-ray playlist {number}")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()

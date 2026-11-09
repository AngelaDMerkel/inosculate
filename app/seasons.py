from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .util import json_load, safe_component, safe_relative_mkv


def primary_title(metadata: dict[str, Any], title_id: str | None = None) -> dict[str, Any] | None:
    titles = metadata.get("titles", [])
    if title_id is not None:
        selected = next(
            (title for title in titles if str(title.get("id")) == str(title_id)),
            None,
        )
        if selected:
            return selected
    return next(
        (title for title in titles if title.get("suggested")),
        titles[0] if titles else None,
    )


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def track_layout(title: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not title:
        return []
    return [
        {
            "type": track.get("type", "other"),
            "ordinal": int(track.get("ordinal", 0) or 0),
            "codec": _normalized(track.get("codec")),
            "language": _normalized(track.get("language") or "und"),
            "name": _normalized(track.get("name")),
            "default": bool(track.get("default")),
            "forced": bool(track.get("forced")),
            "channels": _normalized(track.get("audio_channels")),
            "sample_rate": _normalized(track.get("sample_rate")),
            "width": int(track.get("width", 0) or 0),
            "height": int(track.get("height", 0) or 0),
        }
        for track in title.get("tracks", [])
        if track.get("type") in {"video", "audio", "subtitle"}
    ]


def layout_id(title: dict[str, Any] | None) -> str:
    encoded = json.dumps(
        track_layout(title), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def season_analysis(sources: list[dict[str, Any]]) -> dict[str, Any]:
    inspected: list[tuple[int, str]] = []
    for source in sources:
        if source.get("probe_status") != "ready":
            continue
        title = primary_title(json_load(source.get("metadata_json"), {}))
        if title:
            inspected.append((int(source["id"]), layout_id(title)))

    counts = Counter(signature for _, signature in inspected)
    clusters = [
        {
            "layout_id": signature,
            "count": count,
            "source_ids": [
                source_id
                for source_id, source_signature in inspected
                if source_signature == signature
            ],
        }
        for signature, count in counts.most_common()
    ]
    total = len(sources)
    inspected_count = len(inspected)
    if not inspected_count:
        status = "unknown"
    elif inspected_count < total:
        status = "analyzing"
    elif len(clusters) == 1:
        status = "uniform"
    else:
        status = "mixed"
    return {
        "status": status,
        "total": total,
        "inspected": inspected_count,
        "pending": total - inspected_count,
        "matching": clusters[0]["count"] if clusters else 0,
        "exceptions": inspected_count - (clusters[0]["count"] if clusters else 0),
        "clusters": clusters,
    }


def _track_by_role(title: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(track.get("type")), int(track.get("ordinal", 0) or 0)): track
        for track in title.get("tracks", [])
        if track.get("type") in {"video", "audio", "subtitle"}
    }


def episode_filename(source: dict[str, Any]) -> str:
    series = safe_component(str(source.get("series_name") or "Series"))
    season = int(source.get("season_number") or 1)
    episode = source.get("episode_number")
    if episode is None:
        return f"{safe_component(Path(str(source['path'])).stem)}.mkv"
    title = safe_component(str(source.get("episode_title") or ""), "")
    suffix = f" - {title}" if title else ""
    return f"{series} - S{season:02d}E{int(episode):02d}{suffix}.mkv"


def season_plan(
    template_source: dict[str, Any],
    sources: list[dict[str, Any]],
    body: dict[str, Any],
) -> dict[str, Any]:
    template_metadata = json_load(template_source.get("metadata_json"), {})
    template_title = primary_title(
        template_metadata, str(body.get("title_id", "0"))
    )
    if not template_title:
        raise ValueError("The template source has no selected title")

    available = {
        str(track.get("key")): track for track in template_title.get("tracks", [])
    }
    selected_keys = list(
        dict.fromkeys(
            str(value)
            for value in body.get("selected_tracks", [])
            if str(value) in available
        )
    )
    if not selected_keys:
        raise ValueError("Select at least one track for the season template")
    selected_types = {available[key].get("type") for key in selected_keys}
    if not selected_types.intersection({"video", "audio"}):
        raise ValueError("Select at least one video or audio track")

    template_roles = [
        (
            str(available[key].get("type")),
            int(available[key].get("ordinal", 0) or 0),
            key,
        )
        for key in selected_keys
    ]
    template_layout = layout_id(template_title)
    raw_overrides = body.get("track_overrides", {})
    relative_directory = str(body.get("relative_directory", "")).strip("/")
    planned: list[dict[str, Any]] = []

    for source in sources:
        item = {
            "source_id": int(source["id"]),
            "name": source["name"],
            "season_number": source.get("season_number"),
            "episode_number": source.get("episode_number"),
            "episode_title": source.get("episode_title"),
            "probe_status": source.get("probe_status"),
            "matched": False,
            "reason": "",
        }
        if source.get("probe_status") != "ready":
            item["reason"] = source.get("probe_error") or "Track inspection is incomplete"
            planned.append(item)
            continue
        if int(source.get("active_job_count", 0) or 0):
            item["reason"] = "A remux is already active"
            planned.append(item)
            continue
        if int(source.get("completed_job_count", 0) or 0):
            item["reason"] = "This source was already processed"
            planned.append(item)
            continue

        target_metadata = json_load(source.get("metadata_json"), {})
        target_title = primary_title(target_metadata)
        if not target_title:
            item["reason"] = "No main title was found"
            planned.append(item)
            continue
        if layout_id(target_title) != template_layout:
            item["reason"] = "Track layout differs from the season template"
            planned.append(item)
            continue

        target_roles = _track_by_role(target_title)
        mapped_keys: list[str] = []
        mapped_overrides: dict[str, dict[str, str]] = {}
        missing = False
        for track_type, ordinal, template_key in template_roles:
            target_track = target_roles.get((track_type, ordinal))
            if not target_track:
                missing = True
                break
            target_key = str(target_track.get("key"))
            mapped_keys.append(target_key)
            override = raw_overrides.get(template_key, {})
            if isinstance(override, dict) and (
                override.get("language") or override.get("name") or override.get("clear_name")
            ):
                mapped_overrides[target_key] = {
                    field: override[field] for field in ("language", "name", "clear_name") if field in override
                }
        if missing:
            item["reason"] = "A selected track could not be mapped"
            planned.append(item)
            continue

        filename = episode_filename(source)
        relative_path = safe_relative_mkv(
            f"{relative_directory}/{filename}" if relative_directory else filename
        )
        item.update(
            {
                "matched": True,
                "title_id": str(target_title.get("id", "0")),
                "selected_tracks": mapped_keys,
                "track_overrides": mapped_overrides,
                "relative_path": str(relative_path),
            }
        )
        planned.append(item)

    return {
        "layout_id": template_layout,
        "total": len(planned),
        "matched": sum(bool(item["matched"]) for item in planned),
        "exceptions": sum(not item["matched"] for item in planned),
        "items": planned,
    }

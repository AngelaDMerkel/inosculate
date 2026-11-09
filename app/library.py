"""Source discovery and title inspection within a bounded probe budget."""

import json
from pathlib import Path
from threading import BoundedSemaphore

from .config import settings
from .db import now, row, rows, transaction
from .discs import inspect_title
from .media import enrich_metadata


_probe_slots = BoundedSemaphore(settings.probe_concurrency)


def queue_inspections(source_ids: list[int]) -> int:
    ids = list(dict.fromkeys(map(int, source_ids)))
    if not ids:
        raise ValueError("Choose at least one source to inspect")
    with transaction(immediate=True) as connection:
        changes = 0
        for source_id in ids:
            changes += connection.execute(
                """UPDATE sources SET probe_status='inspection_queued', probe_error=NULL, updated_at=?
                    WHERE id=? AND present=1 AND probe_status IN ('discovered','error')""",
                (now(), source_id),
            ).rowcount
        return changes


def title_details(source_id: int, title_id: str) -> dict:
    source = row("SELECT * FROM sources WHERE id=? AND present=1", (source_id,))
    if not source or source["probe_status"] != "ready":
        raise ValueError("Inspect this source first")
    metadata = json.loads(source["metadata_json"])
    title = next((t for t in metadata["titles"] if t["id"] == title_id), None)
    if not title:
        raise ValueError("Title is no longer in the disc catalog")
    if "tracks" in title:
        return title
    with _probe_slots:
        title = inspect_title(Path(source["path"]), title)
    title = enrich_metadata({"titles": [title]})["titles"][0]
    with transaction(immediate=True) as connection:
        current = connection.execute(
            """SELECT metadata_json FROM sources WHERE id=? AND present=1
                AND probe_status='ready' AND mtime=? AND size=?""",
            (source_id, source["mtime"], source["size"]),
        ).fetchone()
        if not current:
            raise ValueError("The source changed. Inspect it again.")
        metadata = json.loads(current["metadata_json"])
        for index, item in enumerate(metadata["titles"]):
            if item["id"] == title_id:
                metadata["titles"][index] = title
                break
        else:
            raise ValueError("The title changed. Inspect it again.")
        connection.execute("UPDATE sources SET metadata_json=? WHERE id=?",
                           (json.dumps(metadata), source_id))
    return title


def reviewed_files(ids: list[int]) -> list[dict]:
    if not isinstance(ids, list) or not ids:
        raise ValueError("Select at least one file")
    ids = list(dict.fromkeys(map(int, ids)))
    files = rows("""
        SELECT sources.* FROM json_each(?) choice JOIN sources ON sources.id=choice.value
         WHERE sources.present=1 AND sources.kind='file' AND sources.probe_status='ready'
         ORDER BY choice.key
    """, (json.dumps(ids),))
    if len(files) != len(ids):
        raise ValueError("The file selection changed. Inspect the selected files again.")
    return files

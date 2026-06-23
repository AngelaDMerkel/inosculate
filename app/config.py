from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _integer(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    ingest_dir: Path
    movies_dir: Path
    television_dir: Path
    data_dir: Path
    trash_dir: Path | None
    web_host: str
    web_port: int
    remux_concurrency: int
    probe_concurrency: int
    preview_concurrency: int
    stability_seconds: int
    poll_seconds: float

    @property
    def database_path(self) -> Path:
        return self.data_dir / "inosculate.sqlite3"

    @property
    def preview_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"


def load_settings() -> Settings:
    trash_value = os.getenv("TRASH_DIR", "").strip()
    return Settings(
        ingest_dir=Path(os.getenv("INGEST_DIR", "/ingest")).resolve(),
        movies_dir=Path(os.getenv("MOVIES_DIR", "/output/movies")).resolve(),
        television_dir=Path(
            os.getenv("TELEVISION_DIR", "/output/television")
        ).resolve(),
        data_dir=Path(os.getenv("DATA_DIR", "/data")).resolve(),
        trash_dir=Path(trash_value).resolve() if trash_value else None,
        web_host=os.getenv("WEB_HOST", "0.0.0.0"),
        web_port=_integer("WEB_PORT", 8787, 1),
        remux_concurrency=_integer("REMUX_CONCURRENCY", 1, 1),
        probe_concurrency=_integer("PROBE_CONCURRENCY", 3, 1),
        preview_concurrency=_integer("PREVIEW_CONCURRENCY", 2, 1),
        stability_seconds=_integer("STABILITY_SECONDS", 30, 0),
        poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1")),
    )


settings = load_settings()


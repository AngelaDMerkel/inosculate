from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from app import scanner
from app.config import settings
from app.db import initialize
from app.util import format_duration, parse_duration, safe_relative_mkv
from app.worker import _selection_options


class PathTests(unittest.TestCase):
    def test_adds_mkv_extension(self) -> None:
        self.assertEqual(
            safe_relative_mkv("Series/Season 01/Episode"),
            Path("Series/Season 01/Episode.mkv"),
        )

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            safe_relative_mkv("../outside.mkv")

    def test_rejects_absolute_paths(self) -> None:
        with self.assertRaises(ValueError):
            safe_relative_mkv("/outside.mkv")

    def test_sanitizes_unsafe_filename_characters(self) -> None:
        self.assertEqual(safe_relative_mkv("Movie: Name?.mkv"), Path("Movie Name.mkv"))


class DurationTests(unittest.TestCase):
    def test_parses_clock_duration(self) -> None:
        self.assertEqual(parse_duration("01:02:03.5"), 3723.5)

    def test_formats_duration(self) -> None:
        self.assertEqual(format_duration(3723), "1:02:03")


class MuxOptionTests(unittest.TestCase):
    def test_builds_explicit_track_selection(self) -> None:
        grouped = {
            "video": [{"id": 0}],
            "audio": [{"id": 1}, {"id": 3}],
            "subtitle": [],
        }
        self.assertEqual(
            _selection_options(grouped),
            [
                "--video-tracks",
                "0",
                "--audio-tracks",
                "1,3",
                "--no-subtitles",
            ],
        )


class ScannerTests(unittest.TestCase):
    def test_empty_ingest_reports_mount_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ingest = root / "ingest"
            ingest.mkdir()
            original_ingest = settings.ingest_dir
            original_data = settings.data_dir
            object.__setattr__(settings, "ingest_dir", ingest)
            object.__setattr__(settings, "data_dir", root / "data")
            try:
                initialize()
                result = scanner.discover()
            finally:
                object.__setattr__(settings, "ingest_dir", original_ingest)
                object.__setattr__(settings, "data_dir", original_data)
        self.assertEqual(result["found"], 0)
        self.assertIn("mount is empty", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()

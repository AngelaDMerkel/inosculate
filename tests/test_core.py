from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path

from app import scanner
from app.catalog import infer_episode, resolution_class
from app.config import settings
from app.db import initialize, now, rows, transaction
from app.seasons import episode_filename, season_analysis, season_plan
from app.util import format_duration, parse_duration, safe_relative_mkv
from app.worker import _selection_options
from app.web import _insert_prepared_job, _public_source


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

    def test_builds_language_and_name_overrides(self) -> None:
        grouped = {
            "video": [],
            "audio": [
                {
                    "id": 2,
                    "language_override": "eng",
                    "name_override": "Commentary",
                }
            ],
            "subtitle": [],
        }
        self.assertEqual(
            _selection_options(grouped),
            [
                "--no-video",
                "--audio-tracks",
                "2",
                "--language",
                "2:eng",
                "--track-name",
                "2:Commentary",
                "--no-subtitles",
            ],
        )


class WebPayloadTests(unittest.TestCase):
    def test_source_list_payload_summarizes_without_all_track_metadata(self) -> None:
        source = {
            "id": 1,
            "name": "Example.mkv",
            "size": 1024,
            "metadata_json": """
            {"titles":[{"id":"0","duration":3600,"resolution":"1920×800",
            "resolution_class":"1080p","suggested":true,"tracks":[
            {"type":"video"},{"type":"audio"},{"type":"audio"},
            {"type":"subtitle"}]}]}
            """,
        }
        result = _public_source(source, include_metadata=False)
        self.assertNotIn("metadata", result)
        self.assertNotIn("metadata_json", result)
        self.assertEqual(result["media_summary"]["resolution_class"], "1080p")
        self.assertEqual(result["media_summary"]["audio_tracks"], 2)
        self.assertEqual(result["media_summary"]["subtitle_tracks"], 1)

    def test_source_detail_payload_keeps_track_metadata(self) -> None:
        source = {
            "id": 1,
            "name": "Example.mkv",
            "size": 1024,
            "metadata_json": '{"titles":[{"id":"0","tracks":[]}]}',
        }
        result = _public_source(source)
        self.assertIn("metadata", result)
        self.assertEqual(result["metadata"]["titles"][0]["id"], "0")


class CatalogTests(unittest.TestCase):
    def test_groups_standard_television_filename(self) -> None:
        root = Path("/ingest")
        value = infer_episode(
            root / "The Show" / "The.Show.S02E07.The.Test.1080p.mkv",
            root,
        )
        self.assertEqual(value["series_name"], "The Show")
        self.assertEqual(value["season_number"], 2)
        self.assertEqual(value["episode_number"], 7)
        self.assertEqual(value["episode_title"], "The Test")

    def test_groups_sample_directory_filename_styles(self) -> None:
        root = Path("/ingest")
        examples = [
            (
                root
                / "DARKER Than BLACK (2007-2010)"
                / "1a. Season 1 and OVA (2007-08)"
                / "DARKER Than BLACK - S01 E20 - Dreaming Shallow, Uninebriated, Part 2 (720p - DUAL Audio).mkv",
                "DARKER Than BLACK",
                1,
                20,
            ),
            (
                root
                / "Eigen.kweek.Season.1-3.Special.Complete.1080P.WebDL.x264.WildBrian"
                / "Seizoen 3"
                / "[S3.Ep3] Eigen kweek - Wholemoal alone.mp4",
                "Eigen kweek",
                3,
                3,
            ),
            (
                root
                / "Steins;Gate The Ultimate Enthusiast's Collected Edition"
                / "Steins;Gate - 02 ITBD Remux.mkv",
                "Steins;Gate",
                1,
                2,
            ),
            (
                root
                / "[Anime Time] Bleach Thousand-Year Blood War"
                / "Part 2 - The Separation"
                / "Bleach - Thousand-Year Blood War - 026 - Black.mkv",
                "Bleach - Thousand-Year Blood War",
                1,
                26,
            ),
            (
                root / "[Commie] Pluto" / "[Commie] Pluto - 07v2 [6EB11C49].mkv",
                "Pluto",
                1,
                7,
            ),
            (
                root
                / "[a-S] Trigun (01-26)"
                / "[a-s]_trigun_-_15_-_demoris_eye__rs2_[875F2973].mkv",
                "Trigun",
                1,
                15,
            ),
            (
                root
                / "Most.S03.2015.DUAL.BDRip.XviD.AC3.-HQ-ViDEO"
                / "Most.S03.E09.2015.DUAL.BDRip.XviD.AC3.-HQ-ViDEO.avi",
                "Most",
                3,
                9,
            ),
            (
                root
                / "GUNDAM Series Pack"
                / "4a. Turn A Gundam (1999-2000)"
                / "Turn A Gundam - 04 - Soldiers.mkv",
                "Turn A Gundam",
                1,
                4,
            ),
        ]
        for path, series, season, episode in examples:
            with self.subTest(path=path.name):
                value = infer_episode(path, root)
                self.assertEqual(value["series_name"], series)
                self.assertEqual(value["season_number"], season)
                self.assertEqual(value["episode_number"], episode)

    def test_resolution_uses_width_for_cropped_video(self) -> None:
        self.assertEqual(resolution_class(1920, 800), "1080p")
        self.assertEqual(resolution_class(1280, 536), "720p")
        self.assertEqual(resolution_class(3840, 1600), "2160p")


class SeasonTemplateTests(unittest.TestCase):
    @staticmethod
    def source(source_id: int, episode: int, *, codec: str = "AAC") -> dict:
        return {
            "id": source_id,
            "path": f"/ingest/Series.Name.S01E{episode:02d}.mkv",
            "name": f"Series.Name.S01E{episode:02d}.mkv",
            "series_name": "Series Name",
            "season_number": 1,
            "episode_number": episode,
            "episode_title": f"Episode {episode}",
            "probe_status": "ready",
            "active_job_count": 0,
            "completed_job_count": 0,
            "metadata_json": json.dumps(
                {
                    "titles": [
                        {
                            "id": "0",
                            "suggested": True,
                            "tracks": [
                                {
                                    "key": "0",
                                    "type": "video",
                                    "ordinal": 0,
                                    "codec": "AVC",
                                    "width": 1920,
                                    "height": 1080,
                                },
                                {
                                    "key": "1",
                                    "type": "audio",
                                    "ordinal": 0,
                                    "codec": codec,
                                    "language": "eng",
                                    "audio_channels": 2,
                                },
                            ],
                        }
                    ]
                }
            ),
        }

    def test_identifies_uniform_and_mixed_seasons(self) -> None:
        uniform = [self.source(1, 1), self.source(2, 2)]
        self.assertEqual(season_analysis(uniform)["status"], "uniform")
        mixed = [self.source(1, 1), self.source(2, 2, codec="FLAC")]
        result = season_analysis(mixed)
        self.assertEqual(result["status"], "mixed")
        self.assertEqual(result["exceptions"], 1)

    def test_maps_template_tracks_by_semantic_role(self) -> None:
        sources = [self.source(1, 1), self.source(2, 2)]
        result = season_plan(
            sources[0],
            sources,
            {
                "title_id": "0",
                "selected_tracks": ["0", "1"],
                "track_overrides": {"1": {"language": "eng", "name": "Stereo"}},
                "relative_directory": "Series Name/Season 01",
            },
        )
        self.assertEqual(result["matched"], 2)
        self.assertEqual(
            result["items"][1]["track_overrides"]["1"]["name"], "Stereo"
        )
        self.assertEqual(
            result["items"][1]["relative_path"],
            "Series Name/Season 01/Series Name - S01E02 - Episode 2.mkv",
        )

    def test_season_filename_keeps_episode_identity(self) -> None:
        self.assertEqual(
            episode_filename(self.source(1, 7)),
            "Series Name - S01E07 - Episode 7.mkv",
        )


class QueueStateTests(unittest.TestCase):
    def test_a_source_can_only_be_scheduled_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_data = settings.data_dir
            object.__setattr__(settings, "data_dir", Path(directory) / "data")
            try:
                initialize()
                timestamp = now()
                with transaction() as connection:
                    source_id = connection.execute(
                        """
                        INSERT INTO sources
                            (path, name, kind, probe_status, metadata_json,
                             discovered_at, updated_at)
                        VALUES (?, ?, 'file', 'ready', '{}', ?, ?)
                        """,
                        ("/ingest/example.mkv", "example.mkv", timestamp, timestamp),
                    ).lastrowid
                prepared = {
                    "source_id": source_id,
                    "title_id": "0",
                    "output_kind": "movie",
                    "relative_path": "example.mkv",
                    "output_path": "/movies/example.mkv",
                    "selected_tracks": ["0", "1"],
                    "track_overrides": {},
                    "overwrite": False,
                }
                with transaction(immediate=True) as connection:
                    _insert_prepared_job(connection, prepared, timestamp)
                with self.assertRaisesRegex(ValueError, "no longer awaiting"):
                    with transaction(immediate=True) as connection:
                        _insert_prepared_job(connection, prepared, timestamp)
                queued = rows("SELECT * FROM jobs")
                source = rows("SELECT * FROM sources")[0]
            finally:
                object.__setattr__(settings, "data_dir", original_data)
        self.assertEqual(len(queued), 1)
        self.assertEqual(source["queue_state"], "scheduled")


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

    def test_scan_only_inventories_and_does_not_create_ingest_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ingest = root / "ingest"
            ingest.mkdir()
            media = ingest / "Series.Name.S01E02.Pilot.mkv"
            media.write_bytes(b"not opened by inventory scan")
            originals = {
                "ingest_dir": settings.ingest_dir,
                "data_dir": settings.data_dir,
                "trash_dir": settings.trash_dir,
            }
            object.__setattr__(settings, "ingest_dir", ingest)
            object.__setattr__(settings, "data_dir", root / "data")
            object.__setattr__(
                settings, "trash_dir", ingest / ".inosculate-trash"
            )
            try:
                initialize()
                result = scanner.discover()
                sources = rows("SELECT * FROM sources")
                trash_exists = (ingest / ".inosculate-trash").exists()
            finally:
                for key, value in originals.items():
                    object.__setattr__(settings, key, value)
        self.assertEqual(result["found"], 1)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["probe_status"], "discovered")
        self.assertIsNone(sources[0]["metadata_json"])
        self.assertEqual(sources[0]["series_name"], "Series Name")
        self.assertFalse(trash_exists)


if __name__ == "__main__":
    unittest.main()

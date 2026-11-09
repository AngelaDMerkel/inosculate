import array
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.db import initialize, now, row, rows, transaction
from app.discs import bluray_titles, dvd_titles, ffmpeg_input
from app.library import queue_inspections, title_details
from app.media import inspect_output
from app.previews import preview_once
from app.queue import schedule_batch
from app.scanner import discover
from app.worker import probe_once, remux_once
from app.web import Handler


class DiscCatalogTests(unittest.TestCase):
    def test_dvd_keeps_navigation_numbers_and_chapter_boundaries(self):
        xml = """libdvdread message
        <lsdvd><track><ix>7</ix><length>60.5</length>
        <chapter><length>31</length></chapter><chapter><length>29.5</length></chapter>
        </track></lsdvd>"""
        with patch("app.discs.listing", return_value=xml):
            title, = dvd_titles(Path("/disc"))
        self.assertEqual(title["id"], "dvd:7")
        self.assertEqual(title["chapter_starts"], [0, 2790000])
        self.assertNotIn("tracks", title)
        self.assertEqual(ffmpeg_input(Path("/disc.iso"), title["id"])[2:4], ["-title", "7"])

    def test_short_only_bluray_iso_uses_playlist_number_and_stream_reader(self):
        with tempfile.NamedTemporaryFile(suffix=".iso") as image:
            with patch("app.discs.executable", side_effect=lambda name: name), patch("app.discs.run_json", return_value=[
                {"playlist": 42, "ticks": 180000, "starts": [0, 90000]},
            ]):
                title, = bluray_titles(Path(image.name))
        self.assertEqual(title["id"], "bd:00042")
        self.assertTrue(title["stream_playlist"])
        self.assertEqual(title["chapter_starts"], [0, 90000])


class BatchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for key in ["data_dir", "movies_dir", "television_dir", "ingest_dir"]:
            path = root / key
            path.mkdir()
            self.addCleanup(object.__setattr__, settings, key, getattr(settings, key))
            object.__setattr__(settings, key, path)
        self.addCleanup(object.__setattr__, settings, "stability_seconds", settings.stability_seconds)
        object.__setattr__(settings, "stability_seconds", 0)
        initialize()

    def insert_disc(self):
        track = {"key": "0", "id": 0, "type": "video"}
        titles = [{"id": f"dvd:{n}", "tracks": [track]} for n in [1, 2]]
        with transaction() as connection:
            return connection.execute("""
                INSERT INTO sources (path,name,kind,probe_status,metadata_json,discovered_at,updated_at)
                VALUES ('/ingest/disc.iso','Disc','iso','ready',?,?,?)
            """, (json.dumps({"titles": titles}), now(), now())).lastrowid

    def item(self, source, title, output):
        return {"source_id": source, "title_id": title, "selected_tracks": ["0"],
                "output_kind": "movie", "relative_path": output}

    def test_batch_requires_explicit_titles_and_rolls_back_conflicts(self):
        source = self.insert_disc()
        first = self.item(source, "dvd:1", "one.mkv")
        second = self.item(source, "dvd:2", "two.mkv")
        with self.assertRaisesRegex(ValueError, "Choose a title"):
            schedule_batch([{**first, "title_id": ""}])
        with self.assertRaisesRegex(ValueError, "already has a job"):
            schedule_batch([first, {**second, "relative_path": "one.mkv"}])
        self.assertEqual(rows("SELECT * FROM jobs"), [])
        self.assertEqual(row("SELECT queue_state FROM sources")["queue_state"], "awaiting")
        self.assertEqual(len(schedule_batch([first, second])), 2)
        snapshots = [json.loads(j["input_json"])["title"]["id"] for j in rows("SELECT * FROM jobs ORDER BY id")]
        self.assertEqual(snapshots, ["dvd:1", "dvd:2"])

    def test_legacy_disc_jobs_require_reselection_and_keep_history(self):
        source = self.insert_disc()
        pending, completed = schedule_batch([self.item(source, "dvd:1", "one"), self.item(source, "dvd:2", "two")])
        with transaction() as connection:
            connection.execute("PRAGMA user_version=0")
            connection.execute("UPDATE jobs SET title_id='0', input_json=NULL")
            connection.execute("UPDATE jobs SET status='complete' WHERE id=?", (completed,))
        initialize()
        initialize()
        self.assertEqual(row("SELECT status FROM jobs WHERE id=?", (pending,))["status"], "needs_selection")
        self.assertEqual(row("SELECT status FROM jobs WHERE id=?", (completed,))["status"], "complete")
        self.assertIsNone(row("SELECT metadata_json FROM sources")["metadata_json"])

    def test_cleanup_cannot_remove_a_disc_while_another_title_is_queued(self):
        source_id = self.insert_disc()
        path = settings.ingest_dir / "Disc.iso"
        path.write_bytes(b"fixture")
        stat = path.stat()
        with transaction() as connection:
            connection.execute("UPDATE sources SET path=?,size=?,mtime=? WHERE id=?",
                               (str(path), stat.st_size, stat.st_mtime, source_id))
        first, second = schedule_batch([self.item(source_id, "dvd:1", "one"),
                                        self.item(source_id, "dvd:2", "two")])
        with transaction() as connection:
            connection.execute("UPDATE jobs SET status='complete' WHERE id=?", (first,))
            cleanup = connection.execute("INSERT INTO cleanup (job_id,source_id,created_at) VALUES (?,?,?)",
                                         (first, source_id, now())).lastrowid
        handler = object.__new__(Handler)
        with self.assertRaisesRegex(ValueError, "active or retryable"):
            handler._cleanup_action(cleanup, {"confirm": True, "decision": "delete"})
        self.assertTrue(path.exists())
        with transaction() as connection:
            connection.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (second,))
            connection.execute("UPDATE sources SET mtime=mtime+1 WHERE id=?", (source_id,))
        with self.assertRaisesRegex(ValueError, "changed since this output"):
            handler._cleanup_action(cleanup, {"confirm": True, "decision": "delete"})
        self.assertTrue(path.exists())

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("ffmpeg", "mkvmerge", "ffprobe")),
                         "media tools are required for audio preview verification")
    def test_each_preview_uses_the_audio_id_selected_by_the_muxer(self):
        path = settings.ingest_dir / "Two tones.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440",
                        "-f", "lavfi", "-i", "sine=frequency=880", "-map", "0:a", "-map", "1:a",
                        "-t", "2", "-c:a", "flac", str(path)], check=True)
        discover()
        source = row("SELECT * FROM sources")
        queue_inspections([source["id"]])
        self.assertTrue(probe_once())
        metadata = json.loads(row("SELECT metadata_json FROM sources")["metadata_json"])
        tracks = metadata["titles"][0]["tracks"]
        with transaction() as connection:
            for track in tracks:
                # Cross-reader ordinals are not stable track identities.
                track["ordinal"] = 0
                connection.execute("""
                    INSERT INTO previews (source_id,title_id,track_key,created_at,updated_at)
                    VALUES (?,'0',?,?,?)
                """, (source["id"], track["key"], now(), now()))
            connection.execute("UPDATE sources SET metadata_json=?", (json.dumps(metadata),))
        self.assertTrue(preview_once())
        self.assertTrue(preview_once())
        previews = rows("SELECT * FROM previews ORDER BY id")
        self.assertEqual(len(previews), 2)
        for preview, expected_hz in zip(previews, (440, 880)):
            self.assertEqual(preview["status"], "complete", preview["error"])
            data = subprocess.check_output(["ffmpeg", "-v", "error", "-i", preview["path"],
                                            "-ss", "0.2", "-t", "1", "-ac", "1", "-ar", "8000",
                                            "-f", "s16le", "-"])
            samples = array.array("h", data)
            crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
            self.assertAlmostEqual(crossings * 8000 / len(samples), expected_hz, delta=3)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe", "mkvmerge")),
                         "media tools are required for the remux integration test")
    def test_remux_existing_episode_keeps_selected_audio_and_drops_subtitles(self):
        path = settings.ingest_dir / "Example.S02E03.mkv"
        subtitle = settings.data_dir / "sample.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:00,900\nTest\n")
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:r=5",
            "-f", "lavfi", "-i", "sine=frequency=440", "-f", "lavfi", "-i", "sine=frequency=880",
            "-i", str(subtitle), "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
            "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fra", str(path),
        ], check=True)
        digest = hashlib.sha256(path.read_bytes()).digest()
        discover()
        source = row("SELECT * FROM sources")
        queue_inspections([source["id"]])
        self.assertTrue(probe_once())
        title = title_details(source["id"], "0")
        keys = [t["key"] for t in title["tracks"] if t["type"] == "video" or
                (t["type"] == "audio" and t["language"] in {"fra", "fre", "fr"})]
        self.assertEqual(len(keys), 2)
        request = {**self.item(source["id"], "0", "Season 02/Example.S02E03.mkv"),
                   "selected_tracks": keys, "output_kind": "television",
                   "track_overrides": {keys[1]: {"name":"French dialogue","language":"fra"}}}
        schedule_batch([request])
        self.assertTrue(remux_once())
        job = row("SELECT * FROM jobs")
        self.assertEqual(job["status"], "complete", job["error"])
        tracks = inspect_output(Path(job["output_path"]))["titles"][0]["tracks"]
        self.assertEqual([t["type"] for t in tracks], ["video", "audio"])
        self.assertEqual(tracks[1]["name"], "French dialogue")
        self.assertIn(tracks[1]["language"], {"fra", "fre", "fr"})
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)

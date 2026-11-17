import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ci import publish


class ImagePublishTests(unittest.TestCase):
    def test_latest_requires_matching_release_and_a_successful_lookup(self):
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"):
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"id": 5}', '')):
                self.assertTrue(publish.is_latest_release(5))
                self.assertFalse(publish.is_latest_release(4))
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, '', 'HTTP 404')):
                self.assertFalse(publish.is_latest_release(5))
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, '', 'HTTP 403')):
                with self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                    publish.is_latest_release(5)

    def test_promotion_uses_tested_archives_and_never_rebuilds(self):
        self.promotion()

    def test_changed_image_is_rejected_before_any_push(self):
        self.promotion(wrong_id=True)

    def promotion(self, wrong_id=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = "ghcr.io/owner/repo"
            meta = dict(image=image, version="1.2.3-dev.abc123", revision="a" * 40,
                        source="https://github.com/owner/repo", tags=["dev", "sha-" + "a" * 40], stable=False)
            digests = ["sha256:" + char * 64 for char in ("b", "c")]
            for arch in ("amd64", "arm64"):
                folder = root / "artifacts" / f"image-{arch}"
                folder.mkdir(parents=True)
                (folder / "image-metadata.json").write_text(json.dumps(meta))
                (folder / "image-id.txt").write_text(f"tested-{arch}")
            def output(*args):
                if args[:3] == ("docker", "image", "inspect"):
                    arch = args[3].rsplit("-", 1)[1]
                    if args[3].startswith("inosculate:"):
                        return json.dumps([dict(Id="changed" if wrong_id else f"tested-{arch}",
                                                Architecture=arch, Os="linux", Config={"Labels": {
                                                    f"org.opencontainers.image.{key}": meta[key]
                                                    for key in ("source", "version", "revision")}})])
                    return json.dumps([{"RepoDigests": [image + "@" + digests[arch == "arm64"]]}])
                return json.dumps(dict(digest="sha256:" + "d" * 64, manifests=[
                    dict(digest=digest, platform=dict(os="linux", architecture=arch))
                    for arch, digest in zip(("amd64", "arm64"), digests)]))
            original = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, GITHUB_RUN_ID="1", GITHUB_RUN_ATTEMPT="2",
                                GITHUB_OUTPUT=str(root / "output"), GITHUB_STEP_SUMMARY=str(root / "summary")), \
                        patch.object(publish, "output", side_effect=output), patch.object(publish, "run") as run:
                    if wrong_id:
                        with self.assertRaises(AssertionError):
                            publish.main()
                        self.assertFalse(any(call.args[:2] == ("docker", "push") for call in run.call_args_list))
                    else:
                        publish.main()
                        calls = [call.args for call in run.call_args_list]
                        self.assertEqual(sum(call[:2] == ("docker", "load") for call in calls), 2)
                        self.assertEqual(sum(call[:2] == ("docker", "push") for call in calls), 2)
                        self.assertFalse(any("build" in call for call in calls))
                        promotion = next(call for call in calls if "create" in call)
                        self.assertNotIn(image + ":latest", promotion)
                        self.assertEqual(promotion[-2:], tuple(image + "@" + digest for digest in digests))
                        self.assertIn(image + "@sha256:" + "d" * 64, (root / "output").read_text())
            finally:
                os.chdir(original)

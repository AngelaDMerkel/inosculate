import unittest

from ci.image_metadata import metadata, validate_version


class ImageMetadataTests(unittest.TestCase):
    sha = "abc1234" + "0" * 33

    def make(self, event_name="release", tag="v1.2.3", prerelease=False, version="1.2.3", **kwargs):
        event = {"action": "published", "release": {"tag_name": tag, "prerelease": prerelease, "draft": False}}
        event.update(kwargs)
        return metadata(event_name, event, "Example/Inosculate", self.sha, version, version)

    def test_stable_release_has_version_and_full_source_tags(self):
        result = self.make()
        self.assertEqual(result["image"], "ghcr.io/example/inosculate")
        self.assertEqual(result["tags"], ["1.2.3", f"sha-{self.sha}"])
        self.assertTrue(result["stable"])

    def test_unprefixed_version_is_supported(self):
        self.assertEqual(self.make(tag="1.2.3")["version"], "1.2.3")

    def test_suffix_prevents_latest_even_if_release_checkbox_is_wrong(self):
        for prerelease in (False, True):
            with self.subTest(prerelease=prerelease):
                result = self.make(tag="v1.2.3-rc.1", version="1.2.3-rc.1", prerelease=prerelease)
                self.assertFalse(result["stable"])
                self.assertEqual(result["tags"][0], "1.2.3-rc.1")

    def test_prerelease_must_have_a_suffix(self):
        with self.assertRaisesRegex(ValueError, "suffix"):
            self.make(prerelease=True)

    def test_default_branch_uses_development_tags(self):
        result = self.make("push", ref="refs/heads/main", repository={"default_branch": "main"})
        self.assertEqual(result["tags"], ["dev", f"sha-{self.sha}"])
        self.assertEqual(result["version"], f"1.2.3-dev.{self.sha[:12]}")
        self.assertFalse(result["stable"])

    def test_pull_request_has_no_channel_tag(self):
        result = self.make("pull_request", number=7)
        self.assertEqual(result["tags"], [f"sha-{self.sha}"])
        self.assertIn("-pr.7.", result["version"])
        self.assertFalse(result["stable"])

    def test_non_default_push_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "default branch"):
            self.make("push", ref="refs/heads/feature", repository={"default_branch": "main"})

    def test_release_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Release tag"):
            self.make(tag="v2.0.0")

    def test_frontend_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "versions must match"):
            metadata("pull_request", {"number": 1}, "owner/repo", self.sha, "1.0.0", "2.0.0")

    def test_drafts_and_other_events_are_rejected(self):
        for event in ({"action": "created", "release": {}}, {"action": "published", "release": {"draft": True}}):
            with self.assertRaisesRegex(ValueError, "published releases"):
                metadata("release", event, "owner/repo", self.sha, "1.0.0", "1.0.0")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.make("workflow_dispatch")

    def test_invalid_or_unsafe_versions_are_rejected(self):
        for value in ("latest", "1.2", "01.2.3", "1.2.3+build", "1.2.3-01", "1.2.3-rc..1", "1.2.3\nlatest", "1.2.3;echo"):
            with self.subTest(version=value), self.assertRaises(ValueError):
                validate_version(value)

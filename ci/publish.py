"""Promote the tested image archives without rebuilding any layers."""
import json
import os
from pathlib import Path
import re
import subprocess


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def run(*args):
    subprocess.run(args, check=True)


def is_latest_release(release_id):
    response = subprocess.run(
        ["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/releases/latest"],
        capture_output=True, text=True,
    )
    if response.returncode:
        if "HTTP 404" in response.stderr:
            return False  # A release explicitly excluded from latest need not create it.
        raise RuntimeError(f"Cannot determine latest release: {response.stderr}")
    return json.loads(response.stdout)["id"] == release_id


def main():
    root = Path("artifacts")
    meta = json.loads((root / "image-amd64/image-metadata.json").read_text())
    image = meta["image"]
    digests = []
    # Validate *both* artifacts before the first write to the registry.
    for arch in ("amd64", "arm64"):
        folder = root / f"image-{arch}"
        assert json.loads((folder / "image-metadata.json").read_text()) == meta
        run("docker", "load", "--input", str(folder / "image.tar"))
        info = json.loads(output("docker", "image", "inspect", f"inosculate:ci-{arch}"))[0]
        assert info["Id"] == (folder / "image-id.txt").read_text().strip()
        assert info["Architecture"] == arch and info["Os"] == "linux"
        for key in ("source", "revision", "version"):
            assert info["Config"]["Labels"][f"org.opencontainers.image.{key}"] == meta[key]
    for arch in ("amd64", "arm64"):
        transport = f"{image}:build-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-{arch}"
        run("docker", "tag", f"inosculate:ci-{arch}", transport)
        run("docker", "push", transport)
        refs = json.loads(output("docker", "image", "inspect", transport))[0]["RepoDigests"]
        digest = next(ref.split("@", 1)[1] for ref in refs if ref.startswith(image + "@"))
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        digests.append(f"{image}@{digest}")

    tags = list(meta["tags"])
    if meta["stable"]:
        # An older/backport release must not replace the release GitHub marks latest.
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        if is_latest_release(event["release"]["id"]):
            tags.append("latest")
    command = ["docker", "buildx", "imagetools", "create"]
    for tag in tags:
        command += ["--tag", f"{image}:{tag}"]
    run(*command, *digests)
    manifest = json.loads(output("docker", "buildx", "imagetools", "inspect", f"{image}:{tags[0]}",
                                 "--format", "{{json .Manifest}}"))
    digest = manifest["digest"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    assert {m["digest"] for m in manifest["manifests"]} == {ref.split("@")[1] for ref in digests}
    assert {(m["platform"]["os"], m["platform"]["architecture"]) for m in manifest["manifests"]} == {
        ("linux", "amd64"), ("linux", "arm64")}
    reference = f"{image}@{digest}"
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        print(f"reference={reference}", file=handle)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
        print("## Published tested runtime images\n", file=handle)
        print(f"Multi-platform digest: `{reference}`\n", file=handle)
        print("Tags: " + ", ".join(f"`{image}:{tag}`" for tag in tags) + "\n", file=handle)
        for arch, ref in zip(("amd64", "arm64"), digests):
            print(f"- linux/{arch}: `{ref}`", file=handle)


if __name__ == "__main__":
    main()

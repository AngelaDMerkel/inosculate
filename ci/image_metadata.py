"""Validate the existing application versions and derive registry-safe image tags."""
import json
import os
from pathlib import Path
import re
import subprocess


VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?")


def validate_version(value):
    match = VERSION.fullmatch(value)
    if not match or len(value) > 100:
        raise ValueError("Use MAJOR.MINOR.PATCH[-prerelease], without build metadata")
    if match[4] is not None:
        for identifier in match[4].split("."):
            if not identifier or (identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0"):
                raise ValueError("Invalid SemVer prerelease identifier")
    return value


def metadata(event_name, event, repository, revision, app_version, frontend_version):
    validate_version(app_version)
    if app_version != frontend_version:
        raise ValueError("app/__init__.py and frontend/package.json versions must match")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected the full source commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository name")
    stable = False
    tags = [f"sha-{revision}"]
    base = app_version.split("-", 1)[0]
    if event_name == "release":
        release = event["release"]
        if event.get("action") != "published" or release.get("draft"):
            raise ValueError("Only published releases may publish release images")
        version = validate_version(release["tag_name"].removeprefix("v"))
        if version != app_version:
            raise ValueError("Release tag must match both application versions")
        if release["prerelease"] and "-" not in version:
            raise ValueError("Prereleases require a version suffix, for example 1.1.0-rc.1")
        stable = not release["prerelease"] and "-" not in version
        tags.insert(0, version)
    elif event_name == "push":
        if event["ref"] != f"refs/heads/{event['repository']['default_branch']}":
            raise ValueError("Only the default branch may publish development images")
        version = f"{base}-dev.{revision[:12]}"
        tags.insert(0, "dev")
    elif event_name == "pull_request":
        version = f"{base}-pr.{int(event['number'])}.{revision[:12]}"
    else:
        raise ValueError("Unsupported image event")
    return dict(image=f"ghcr.io/{repository.lower()}", version=version, revision=revision,
                source=f"https://github.com/{repository}", tags=tags, stable=stable)


def main():
    import ast

    # Read the version without importing application code on the CI host.
    module = ast.parse(Path("app/__init__.py").read_text())
    version = next(ast.literal_eval(node.value) for node in module.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
    result = metadata(os.environ["GITHUB_EVENT_NAME"],
                      json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
                      os.environ["GITHUB_REPOSITORY"],
                      subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                      version, json.loads(Path("frontend/package.json").read_text())["version"])
    Path("image-metadata.json").write_text(json.dumps(result, indent=2) + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key in ("image", "version", "revision", "source"):
            print(f"{key}={result[key]}", file=output)


if __name__ == "__main__":
    main()

# Inosculate

Inosculate remuxes on-disk DVD and Blu-ray backups, movies, and television episodes into MKV files. A local web interface handles title and track selection. A separate worker reads the chosen streams and verifies each output before publishing it.

## Run with Docker Compose

The web service and worker use the same production image:
`ghcr.io/angeladmerkel/inosculate`. Published images support **linux/amd64** and
**linux/arm64**. Docker selects the matching platform automatically. See
[release tags](#image-tags-and-releases) before choosing a version; `latest` exists
only after the first stable release. Development builds are available as `dev`
after a successful default-branch publishing run.

Install Docker Engine (or Docker Desktop using Linux containers) and Docker Compose
v2 or later. Download `compose.yml` and `.env.example` from the same release, or
check out that release. Copy `.env.example` to `.env` beside `compose.yml` and set:

| Compose variable | Purpose | Container path |
| --- | --- | --- |
| `INOSCULATE_IMAGE` | Full GHCR image reference; prefer an exact version or digest | Same image for both services |
| `INGEST_PATH` | Required import directory containing backups and media | `/ingest` |
| `MOVIES_PATH` | Required movie output directory | `/output/movies` |
| `TELEVISION_PATH` | Required episode output directory | `/output/television` |
| `APP_DATA_PATH` | Persistent SQLite database, previews, work files; defaults to `./data` | `/data` |
| `WEB_PORT` | Published host TCP port; defaults to `8787` | `8787` |

Paths refer to the **Docker host**, not the computer running a remote Docker client.
Relative paths resolve beside `compose.yml`. Create the directories before starting
and use ownership or ACLs to grant UID **568**, GID **568** read/traverse access to
imports and write access to output and app-data directories. Both services must
share the same mounts and image version. `/data` requires storage that supports
SQLite locking; use local storage for the database. `/config` is a writable home
inside the image, not application state, and does not need persistence.

```sh
cp .env.example .env
# Edit .env, create the selected directories, and grant the access described above.
docker compose pull
docker compose up -d
```

Open `http://localhost:8787` when Docker runs locally, or use your Docker host's
address and chosen port. Only the web service publishes a port; the worker has no
inbound port. **The application has no authentication.** Keep access on a trusted
network, or add access control with a reverse proxy/firewall before exposing it.

Compose sets `TRASH_DIR=/ingest/.inosculate-trash`. Imports need write access for
trash moves and explicitly confirmed permanent deletion. If those actions are not
needed, mount imports read-only in a Compose override. Do not use an empty
`TRASH_DIR` to disable cleanup: it selects permanent deletion instead of trash.
Back up `/data` and your media independently; container replacement does not back
up or remove these host directories.

Optional `.env` settings are `REMUX_CONCURRENCY=1`, `PROBE_CONCURRENCY=3`,
`PREVIEW_CONCURRENCY=2`, and `STABILITY_SECONDS=30` (seconds before inspecting a
changed file). Start with these defaults and adjust for CPU and storage capacity.
For direct Docker use, the corresponding path settings are `INGEST_DIR`,
`MOVIES_DIR`, `TELEVISION_DIR`, and `DATA_DIR`; their defaults match the paths above.
`WEB_HOST` defaults to `0.0.0.0`, container `WEB_PORT` to `8787`, and
`WORKER_POLL_SECONDS` to `1`. Compose's `WEB_PORT` controls only the host mapping.

### Direct Docker use

Compose is recommended for managing the pair. The equivalent commands below use
named volumes for generated data and output, and an existing `ingest` directory in
the current directory. New named volumes need writable ownership for UID/GID 568;
Docker initializes the data volume from the image's `/data`, while the output
volumes must be prepared by the operator. Set `IMAGE` to a published reference.

```sh
IMAGE=ghcr.io/angeladmerkel/inosculate:latest
docker volume create inosculate-data
docker volume create inosculate-movies
docker volume create inosculate-television
# Grant UID/GID 568 write access to the output volumes before starting.
for service in web worker; do
  if [ "$service" = web ]; then
    set -- -p 8787:8787
  else
    set --
  fi
  docker run -d --name "inosculate-$service" --restart unless-stopped "$@" \
    --mount "type=bind,src=$(pwd)/ingest,dst=/ingest" \
    --mount type=volume,src=inosculate-data,dst=/data \
    --mount type=volume,src=inosculate-movies,dst=/output/movies \
    --mount type=volume,src=inosculate-television,dst=/output/television \
    -e TRASH_DIR=/ingest/.inosculate-trash "$IMAGE" "$service"
done
```

### Supported platforms

AMD64 and ARM64 are built and tested on native Linux GitHub runners. The Debian
Trixie runtime packages [FFmpeg](https://packages.debian.org/trixie/ffmpeg),
[MKVToolNix](https://packages.debian.org/trixie/mkvtoolnix),
[libbluray](https://packages.debian.org/trixie/libbluray-bin), and
[lsdvd](https://packages.debian.org/trixie/lsdvd), as well as the Debian, Python 3.13,
and Node 22 base images, provide both architectures. The libbluray helper is
compiled separately on each platform; no host binaries are copied into the image.

Other architectures, 32-bit ARM, and Windows containers are not part of the tested
support matrix. macOS and Windows hosts need a Linux VM such as Docker Desktop.
No GPU, optical-drive passthrough, privileged mode, or host networking is required.
Runtime checks verify FFmpeg's `dvdvideo` demuxer and `bluray` protocol, tool loading,
and generated-media remux/preview behavior. Real DVD/Blu-ray navigation is covered
by unit fixtures, not a full physical-disc or encrypted-media test library.

## Review a source and its tracks

1. In **Library**, scan the import folder and click **Review** beside a source. Its editor opens in place. For a disc, choose the DVD title or Blu-ray playlist explicitly.
2. Each video, audio, and subtitle row has its own inclusion checkbox. Click **Listen** beside an audio track, then play its sample. You can listen while the track is unchecked. The player identifies the source, title, and track ID. **Preview subtitles** shows text or a frame from the selected subtitle stream.
3. Choose **Preferred**, **Minimal**, or **All tracks**, or check tracks individually. You can correct the language and name on audio and subtitle rows. **Settings** controls preferred audio and subtitle languages, forced subtitles, and ignored scan patterns.
4. Choose Movies or Television, select an existing directory or enter a new subdirectory, and review the full destination. **Schedule remux** queues this title directly. **Add to batch** carries your exact track choices, labels, and destination into **Batch**.
5. For television files, **Analyze season** compares track layouts. **Apply as season template** shows the matching episodes and exceptions, lets you select episodes, and schedules only after you confirm the preflight.

For bulk file cleanup, select files in **Library** and choose **Review files**. In **Batch**, apply language choices across files or expand **Tracks** to adjust or sample an individual track. **Queue jobs** submits the batch. **Jobs** shows progress, errors, logs, cancellation, and retry.

**Processed** lists verified outputs and their original paths. Choose **Keep original**, **Decide later**, **Move to trash**, or **Delete permanently**. Unscheduled sources have their own **Original source actions** in the editor, including defer and resume. Trash and deletion require confirmation. Write access to the import mount is needed for those two actions.

## Media readers

| Input | Title enumeration | Muxer |
| --- | --- | --- |
| DVD `VIDEO_TS` or DVD ISO | `lsdvd` reads the DVD navigation table | FFmpeg's `dvdvideo` demuxer opens the selected DVD title |
| Blu-ray `BDMV` | libbluray lists playlists | MKVToolNix reads the selected MPLS and its referenced clips |
| Blu-ray ISO | libbluray lists playlists inside the image | FFmpeg opens the image with the selected playlist |
| Existing media file | MKVToolNix and FFprobe identify tracks | MKVToolNix, or FFmpeg for inputs identified by FFprobe alone |

The container includes the disc readers. Inputs should be readable, decrypted backups. ISO images are read in place. No hardware drive, MakeMKV key, or full-size intermediate MKV is required. DVD and Blu-ray PCM may need lossless repacking for Matroska. Disc chapter times are read from the navigation tables and carried into the output. Short-only Blu-ray ISOs stream their selected playlist through libbluray.

The worker defaults to one remux at a time. `REMUX_CONCURRENCY` and `PROBE_CONCURRENCY` control parallelism on the storage host. A failed title leaves other jobs able to continue.

## Image tags and releases

| Tag | Meaning |
| --- | --- |
| `1.2.3` | Exact stable release version (example, not a promise that this version exists) |
| `1.2.3-rc.1` | Explicit prerelease version; never updates `latest` |
| `latest` | The stable release designated latest by GitHub; never a development build |
| `dev` | Most recently published default-branch development build |
| `sha-<full-40-character-commit>` | Source commit of a published build |
| `@sha256:<digest>` | Immutable multi-platform index or platform manifest reference |

Prefer a digest for byte-for-byte reproducibility. Version and commit tags are
traceability references, not registry-enforced immutable names: retrying a build
can incorporate updated base images or Debian packages. Both services must use the
same selected reference. `build-<run>-<attempt>-<architecture>` tags are intermediate
publication artifacts, not release channels.

The repository started with matching `1.0.0` values in `app/__init__.py` and
`frontend/package.json`, and no release/tag automation. Releases keep that SemVer
convention. Maintainers should:

1. Set both version fields to the intended `MAJOR.MINOR.PATCH` or
   `MAJOR.MINOR.PATCH-prerelease` value, commit, and merge the reviewed change.
2. Tag that commit as `v<version>` (an unprefixed `<version>` also works), and
   **publish a GitHub Release** for the tag. A tag push alone does not publish an
   image. Draft releases do not publish. A release marked prerelease must include
   a suffix such as `-rc.1`; build metadata (`+...`) is not supported in image tags.
3. Check the **Containers** workflow. A version mismatch fails before publication.
   Suffix versions remain prereleases even if the GitHub prerelease checkbox was
   missed. Only a stable version whose release is GitHub's designated latest can
   update `latest`; publishing a backport does not automatically promote it.
4. Use the multi-platform and per-platform digests in the publishing job summary.
   Confirm both **Anonymous pull** jobs pass before announcing public availability.

Default-branch pushes publish only `dev` and a source-commit tag, with an OCI version
like `1.0.0-dev.<commit>`. PRs build both platforms and run the same tests without
logging into GHCR or publishing. Builds compile and type-check the frontend, run
all Python tests in the final runtime image (skips fail CI), and smoke-test the
installed web/worker entrypoint, health endpoint, frontend assets, media tools,
and writable directories as UID/GID 568. Publishing loads the saved, tested image
archives, verifies their image IDs/architecture/labels, pushes those exact images,
and combines their digests without rebuilding. Both architecture tests must pass
before publication. Source, full revision, and version are OCI image labels.

Actions are pinned to verified upstream commit SHAs. The build jobs have only
`contents: read`; only publishing receives `packages: write` and uses the automatic
`GITHUB_TOKEN`. No PAT or registry secret is required. BuildKit caches are separated
by architecture and follow GitHub's branch cache isolation. PR runs cancel older
PR runs; publication is serialized. GitHub may replace an older pending run with a
newer one; rerun a superseded release if it still needs publishing. Test archives
expire after two days, so rerun the whole workflow if they are no longer available.

### One-time maintainer setup for public access

Enable GitHub Actions and allow the pinned `actions/*` and `docker/*` Actions and
both `ubuntu-24.04` / `ubuntu-24.04-arm` runners. Protect `main` with both
**Test runtime** checks and restrict release/tag creation to trusted maintainers.
For a fork or renamed repository, update the main-branch trigger and default image
in Compose/examples; workflow image names derive from the lowercase repository
owner/name automatically.

A public source repository does **not** automatically make its GHCR package public.
After the first successful push, open the owner's **Packages → inosculate → Package
settings**, set **Change visibility → Public**, and confirm it is linked to this
repository. Under **Manage Actions access**, grant this repository **Write** if
access was not inherited (particularly when a package with this name already
exists). See GitHub's [package access and visibility guide](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).
Changing source-repository visibility is a separate maintainer choice; publication
does not change it.

The first run may publish successfully and then fail anonymous verification while
the package is still private. Set visibility, then rerun the failed anonymous-pull
jobs. They use an empty Docker configuration on fresh runners, pull the exact
published digest for each platform, and rerun runtime tests. Registry/network errors
also fail these checks. To verify manually, replace the example digest below with
the reported multi-platform digest (no login is needed):

```sh
anonymous_config=$(mktemp -d)
DOCKER_CONFIG="$anonymous_config" docker pull "ghcr.io/angeladmerkel/inosculate@sha256:<reported-digest>"
rm -rf "$anonymous_config"
```

Before the first publication, anonymous access and real registry digests cannot be
verified. A passing PR build does not prove that the package is publicly pullable.

## Upgrading an existing installation

Read the release notes, wait for active work to finish, stop **both** services, and
back up the app-data directory with SQLite stopped. Keep a copy of the previous
image digest and Compose/environment configuration. Set `INOSCULATE_IMAGE` to the
new version or digest, then run:

```sh
docker compose stop
# Back up the configured APP_DATA_PATH and edit INOSCULATE_IMAGE in .env.
docker compose pull
docker compose up -d
docker compose logs --tail=100 web worker
```

Keep the same mounts and verify `/api/health` and the web UI. Database migrations
run at startup. To roll back, stop both services, restore the pre-upgrade app-data
backup and previous image/configuration, and restart; do not assume an older image
can read a newer schema. Never remove persistent volumes as part of an upgrade.

When upgrading from the original MakeMKV-based implementation, the migration keeps
completed jobs and ordinary media-file jobs. Old disc jobs with pending work appear
as **needs selection**. Inspect those discs and choose titles again: MakeMKV title
numbers cannot reliably identify native DVD titles or Blu-ray playlists.

## Development

```sh
python3 -m unittest discover -s tests
cd frontend
pnpm install --frozen-lockfile
pnpm run build
```

Build and validate the runtime locally from the repository root:

```sh
docker build -t inosculate:local .
bash ci/test-image.sh inosculate:local
```

To run Compose from local source, use the explicit override (it uses
`inosculate:local` for both services):

```sh
docker compose -f compose.yml -f compose.build.yml up --build -d
```

The browser application is built into `app/static`. For native operation, use
Python 3.11+, MKVToolNix, FFmpeg with the dvdvideo demuxer and bluray protocol, lsdvd,
bd_splice, and libbluray. Compile `native/bluray_catalog.c` against libbluray and put
`inosculate-bluray-catalog` on PATH. Set `INGEST_DIR`, `MOVIES_DIR`, `TELEVISION_DIR`,
and `DATA_DIR` and run `python3 -m app web` and `python3 -m app worker` separately.

# Inosculate

Inosculate is a small, local-only web application for inspecting mixed media
sources and remuxing selected video, audio, and subtitle tracks into MKV files.
It understands ordinary media files, ISO images, DVD `VIDEO_TS` directories,
and Blu-ray `BDMV` directories.

## What it does

- Manually scans a read/write ingest directory.
- Uses MKVToolNix, FFprobe, and MakeMKV to discover titles and tracks.
- Suggests the longest title as the main feature for disc sources.
- Lets you include or exclude individual video, audio, and subtitle tracks.
- Generates a one-minute audio preview only when Play is requested.
- Guides movie and television destination naming.
- Runs remuxes through a persistent SQLite-backed queue.
- Shows progress and logs, with cancellation, retry, and overwrite decisions.
- Creates a cleanup decision after a successful, validated remux so the source
  can be kept, deferred, moved to trash, or permanently deleted.

## Quick start

Copy `.env.example` to `.env`, set the four host paths, then run:

```sh
docker compose pull
docker compose up -d
```

Open `http://YOUR-NAS:8787`.

The published x86-64 image is
[`angeladmerkel/inosculate`](https://hub.docker.com/r/angeladmerkel/inosculate).
Both the web and worker services use the same image with different commands.
The default [compose.yml](compose.yml) contains no build instructions and
pulls `docker.io/angeladmerkel/inosculate:0.1.1` directly.

For a local source build instead, explicitly include the build override:

```sh
docker compose -f compose.yml -f compose.build.yml up --build -d
```

The container runs as UID `568`; grant that account read/write access to the
ingest, Movies, Television, and app-data datasets. The web interface has no
authentication and should not be exposed outside the trusted local network.

The image expects a valid MakeMKV beta or purchased key in `MAKEMKV_KEY`.
`MAKEMKV_VERSION` is only used when building locally with
`compose.build.yml`; its checked-in default is `1.18.4`.

## TrueNAS mounts

The default Compose layout is:

| Host setting | Container path | Mode |
| --- | --- | --- |
| `INGEST_PATH` | `/ingest` | read/write |
| `MOVIES_PATH` | `/output/movies` | read/write |
| `TELEVISION_PATH` | `/output/television` | read/write |
| `APP_DATA_PATH` | `/data` | read/write |

All values on the left are paths on the machine running Docker. For TrueNAS,
they normally begin with `/mnt/...`. A path such as
`/Volumes/supertanker_media/...` is a macOS SMB client mount and will produce
an empty directory when used as a bind source on the NAS.

In TrueNAS, open the SMB share configuration and copy its local **Path** into
the Portainer stack environment variables. Grant UID `568` read/write and
traverse access to the ingest and output datasets.

The application never renames source media. It writes to a `.partial` file
beside the destination, validates it, and atomically moves it into place.
Compose defaults approved cleanup to `/ingest/.inosculate-trash`, keeping the
move on the ingest filesystem. To make approved deletion permanent instead,
set `TRASH_DIR` to an empty value in a local Compose override.

## Worker defaults

- 1 heavy remux
- 3 metadata probes
- 2 on-demand audio previews

These are configurable through `REMUX_CONCURRENCY`, `PROBE_CONCURRENCY`, and
`PREVIEW_CONCURRENCY`. One remux is the recommended default for a single NAS
storage pool. Run one worker service; concurrency is managed inside it.

## Development

The application itself uses only the Python standard library. To run it
without Docker, install `mkvtoolnix`, `ffmpeg`, and `makemkvcon`, then:

```sh
export INGEST_DIR=/path/to/ingest
export MOVIES_DIR=/path/to/movies
export TELEVISION_DIR=/path/to/television
export DATA_DIR=/tmp/inosculate
python -m app init
python -m app worker
python -m app web
```

The worker and web commands should run in separate terminals.

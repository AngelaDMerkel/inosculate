# Inosculate

Inosculate remuxes on-disk DVD and Blu-ray backups, movies, and television episodes into MKV files. A local web interface handles title and track selection. A separate worker reads the chosen streams and verifies each output before publishing it.

## Run with Docker Compose

Copy `.env.example` to `.env` beside `compose.yml`. Set the three media paths to directories on the Docker host. Both services use the same mounts.

| Compose variable | Purpose | Container path |
| --- | --- | --- |
| `INGEST_PATH` | Import directory containing disc backups and existing media files | `/ingest` |
| `MOVIES_PATH` | Movie output directory | `/output/movies` |
| `TELEVISION_PATH` | Episode output directory | `/output/television` |
| `APP_DATA_PATH` | Persistent database and preview files | `/data` |

For example, a TrueNAS installation can use the following host paths. Grant UID 568 read and traverse access to the import directory and write access to the output and app-data directories.

```dotenv
INGEST_PATH=/mnt/tank/media/incoming
MOVIES_PATH=/mnt/tank/media/Movies
TELEVISION_PATH=/mnt/tank/media/Television
APP_DATA_PATH=/mnt/tank/apps/inosculate
```

```sh
docker compose up --build -d
```

Open `http://YOUR-NAS:8787` on your trusted local network. `WEB_PORT` changes the host port. Compose builds the image from this checkout. The service has no authentication.

## Select a batch

1. In **Library**, scan the import folder. Open a source to inspect it, or choose **Inspect visible** to catalog the filtered list in the background.
2. For each disc, add the titles or playlists you want. Duration and chapter count help distinguish features, episodes, and extras. Scanning and inspection leave every disc title unselected.
3. For existing episodes or movies, select individual files or all visible files. Inspect them and choose **Review files** to add them together.
4. In **Batch**, choose Movies or Television and a relative filename for each output. All supported tracks start selected. Use **Batch track selection** to keep particular audio or subtitle languages or drop all subtitles. Expand **Tracks** to sample audio or subtitles and remove individual streams such as commentary. When a requested audio language is missing, the previous audio choice is retained for that file.
5. **Queue jobs** submits the whole batch. **Jobs** shows progress, errors, logs, cancellation, and retry. Each title produces its own file. The worker checks track counts and duration before publishing, and keeps the original source.

## Media readers

| Input | Title enumeration | Muxer |
| --- | --- | --- |
| DVD `VIDEO_TS` or DVD ISO | `lsdvd` reads the DVD navigation table | FFmpeg's `dvdvideo` demuxer opens the selected DVD title |
| Blu-ray `BDMV` | libbluray lists playlists | MKVToolNix reads the selected MPLS and its referenced clips |
| Blu-ray ISO | libbluray lists playlists inside the image | FFmpeg opens the image with the selected playlist |
| Existing media file | MKVToolNix and FFprobe identify tracks | MKVToolNix, or FFmpeg for inputs identified by FFprobe alone |

The container includes the disc readers. Inputs should be readable, decrypted backups. ISO images are read in place. No hardware drive, MakeMKV key, or full-size intermediate MKV is required. DVD and Blu-ray PCM may need lossless repacking for Matroska. Disc chapter times are read from the navigation tables and carried into the output. Short-only Blu-ray ISOs stream their selected playlist through libbluray.

The worker defaults to one remux at a time. `REMUX_CONCURRENCY` and `PROBE_CONCURRENCY` control parallelism on the storage host. A failed title leaves other jobs able to continue.

## Upgrading an existing installation

Stop the previous services and back up the app-data directory. Use the same `APP_DATA_PATH` when starting this version. The database migration keeps completed jobs and ordinary media-file jobs. Old disc jobs with pending work appear as **needs selection**. Inspect those discs and choose their titles again because MakeMKV title numbers cannot identify native DVD titles or Blu-ray playlists reliably.

## Development

```sh
python3 -m unittest discover -s tests
cd frontend
pnpm install --frozen-lockfile
pnpm run build
```

The browser application is built into `app/static`. For native operation, use Python 3.11+, MKVToolNix, FFmpeg with the dvdvideo demuxer and bluray protocol, lsdvd, bd_splice, and libbluray. Compile `native/bluray_catalog.c` against libbluray and put the resulting `inosculate-bluray-catalog` executable on PATH. Set `INGEST_DIR`, `MOVIES_DIR`, `TELEVISION_DIR`, and `DATA_DIR` and run `python3 -m app web` and `python3 -m app worker` in separate terminals.

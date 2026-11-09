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

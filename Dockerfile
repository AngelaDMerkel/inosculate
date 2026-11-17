FROM debian:trixie-slim AS disc-tools
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libbluray-dev
COPY native/bluray_catalog.c /src/bluray_catalog.c
RUN cc -O2 -Wall -Wextra -o /inosculate-bluray-catalog /src/bluray_catalog.c -lbluray

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/index.html frontend/tsconfig.json frontend/tsconfig.app.json \
     frontend/tsconfig.node.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN pnpm run build

FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg libbluray-bin lsdvd mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

RUN ffmpeg -hide_banner -demuxers | grep -qw dvdvideo \
    && ffmpeg -hide_banner -protocols | grep -qw bluray

COPY --from=disc-tools /inosculate-bluray-catalog /usr/local/bin/

WORKDIR /app
COPY app /app/app
COPY --from=frontend-builder /app/static /app/app/static
COPY docker-entrypoint.sh /usr/local/bin/inosculate
RUN chmod +x /usr/local/bin/inosculate \
    && groupadd --system --gid 568 inosculate \
    && useradd --system --uid 568 --gid 568 --create-home --home-dir /config inosculate \
    && mkdir -p /data /ingest /output/movies /output/television \
    && chown -R inosculate:inosculate /data /config

USER 568:568
EXPOSE 8787
ENTRYPOINT ["inosculate"]
CMD ["web"]

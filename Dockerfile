FROM debian:bookworm-slim AS makemkv-builder

ARG MAKEMKV_VERSION=1.18.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl libc6-dev libavcodec-dev libexpat1-dev \
       libgl1-mesa-dev libssl-dev pkg-config zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl --http1.1 --retry 5 --retry-all-errors --retry-delay 3 \
      --connect-timeout 30 -fsSLO \
      "https://www.makemkv.com/download/makemkv-oss-${MAKEMKV_VERSION}.tar.gz" \
    && curl --http1.1 --retry 5 --retry-all-errors --retry-delay 3 \
      --connect-timeout 30 -fsSLO \
      "https://www.makemkv.com/download/makemkv-bin-${MAKEMKV_VERSION}.tar.gz" \
    && tar -xzf "makemkv-oss-${MAKEMKV_VERSION}.tar.gz" \
    && tar -xzf "makemkv-bin-${MAKEMKV_VERSION}.tar.gz" \
    && cd "makemkv-oss-${MAKEMKV_VERSION}" \
    && ./configure --disable-gui \
    && make -j"$(nproc)" \
    && make install DESTDIR=/stage \
    && cd "../makemkv-bin-${MAKEMKV_VERSION}" \
    && mkdir -p tmp \
    && printf 'accepted\n' > tmp/eula_accepted \
    && make -j"$(nproc)" \
    && make install DESTDIR=/stage
RUN mkdir -p /licenses \
    && cp "makemkv-bin-${MAKEMKV_VERSION}/src/eula_en_linux.txt" \
       /licenses/MakeMKV-EULA.txt \
    && cp "makemkv-oss-${MAKEMKV_VERSION}/License.txt" \
       /licenses/MakeMKV-OSS-License.txt \
    && cp "makemkv-oss-${MAKEMKV_VERSION}/mmccextr/License.txt" \
       /licenses/MakeMKV-mmccextr-License.txt

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/index.html frontend/tsconfig.json frontend/tsconfig.app.json \
     frontend/tsconfig.node.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN pnpm run build

FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates ffmpeg libexpat1 libgl1 libssl3 mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

COPY --from=makemkv-builder /stage/usr /usr
COPY --from=makemkv-builder /licenses /usr/share/doc/makemkv

WORKDIR /app
COPY app /app/app
COPY --from=frontend-builder /app/static /app/app/static
COPY docker-entrypoint.sh /usr/local/bin/inosculate
RUN chmod +x /usr/local/bin/inosculate \
    && useradd --system --uid 568 --create-home --home-dir /config inosculate \
    && mkdir -p /data /ingest /output/movies /output/television \
    && chown -R inosculate:inosculate /data /config

USER inosculate
EXPOSE 8787
ENTRYPOINT ["inosculate"]
CMD ["web"]

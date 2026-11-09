export interface Source {
  id: number;
  name: string;
  path: string;
  kind: "file" | "iso" | "bluray" | "dvd";
  size: number;
  mtime: number;
  probe_status: string;
  probe_error?: string;
  queue_state: string;
  episode_title?: string;
  series_name?: string;
  season_number?: number;
  episode_number?: number;
}
export interface Track {
  key: string;
  type: string;
  language: string;
  codec: string;
  name: string;
  forced?: boolean;
  default?: boolean;
  audio_channels?: number;
  sample_rate?: number;
  resolution?: string;
  resolution_class?: string;
}
export interface Title {
  id: string;
  name: string;
  duration: number;
  chapters?: number;
  resolution?: string;
  resolution_class?: string;
  tracks?: Track[];
}
export interface Detail extends Source {
  metadata: { titles?: Title[] };
}
export interface Draft {
  source: Source;
  title: Title;
  keys: string[];
  library: string;
  filename: string;
  overrides?: Record<string, { language?: string; name?: string; clear_name?: boolean }>;
}
export interface Job {
  id: number;
  source_name: string;
  title_id: string;
  relative_path: string;
  output_path: string;
  phase: string;
  status: string;
  progress: number;
  error?: string;
  cancel_requested: boolean;
  started_at?: string;
  created_at: string;
}

export interface Settings {
  ingest: string;
  movies: string;
  television: string;
  trash_enabled: boolean;
  languages: { code: string; name: string }[];
  concurrency: { remux: number; probe: number; preview: number };
}
export interface Preferences {
  preferred_audio_language: string;
  preferred_subtitle_language: string;
  forced_subtitles_only: boolean;
  ignored_patterns: string[];
}

export class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly output?: string,
  ) {
    super(message);
  }
}

export async function api<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(
    "/api/" + url,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new RequestError(
      error.error || `Request failed (${response.status})`,
      response.status,
      error.code,
      error.output_path,
    );
  }
  return response.json();
}

export const formats = {
  iso: "ISO image",
  dvd: "DVD folder",
  bluray: "Blu-ray folder",
  file: "Media file",
};
export const duration = (value: number) => {
  const seconds = Math.floor(value || 0);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map((n, i) => (i ? String(n).padStart(2, "0") : n))
    .join(":");
};
export const draftKey = (source: Source, title: Title) => source.id + "/" + title.id;
export function draftFor(source: Source, title: Title): Draft {
  const suffix = source.kind === "file" ? "" : " - " + title.name;
  const season = String(source.season_number ?? 1).padStart(2, "0");
  const prefix = source.series_name ? `${source.series_name}/Season ${season}/` : "";
  return {
    source,
    title,
    keys: (title.tracks || [])
      .filter((t) => ["video", "audio", "subtitle"].includes(t.type))
      .map((t) => t.key),
    library: source.series_name ? "television" : "movie",
    filename: prefix + source.name + suffix + ".mkv",
  };
}

export function jobRequest(draft: Draft) {
  return {
    source_id: draft.source.id,
    mtime: draft.source.mtime,
    title_id: draft.title.id,
    output_kind: draft.library,
    relative_path: draft.filename,
    selected_tracks: draft.keys,
    track_overrides: draft.overrides || {},
  };
}

export function sameLanguage(a: string, b: string) {
  if (!a || !b) return false;
  try {
    return new Intl.Locale(a).language === new Intl.Locale(b).language;
  } catch {
    return a.toLowerCase() === b.toLowerCase();
  }
}

export function presetKeys(
  title: Title,
  prefs: Preferences,
  preset: "Preferred" | "Minimal" | "All tracks",
) {
  const tracks = title.tracks || [];
  const audio = tracks.filter((t) => t.type === "audio");
  const preferred = audio.filter((t) => sameLanguage(t.language, prefs.preferred_audio_language));
  const defaultAudio = audio.find((t) => t.default) || audio[0];
  return tracks
    .filter((t) => {
      if (t.type === "other") return false;
      if (preset === "All tracks" || t.type === "video") return true;
      if (t.type === "audio")
        return preset === "Preferred" && preferred.length
          ? preferred.includes(t)
          : t === defaultAudio;
      return (
        preset === "Preferred" &&
        sameLanguage(t.language, prefs.preferred_subtitle_language) &&
        (!prefs.forced_subtitles_only || t.forced)
      );
    })
    .map((t) => t.key);
}

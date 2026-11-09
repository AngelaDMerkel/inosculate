export interface Source {
  id: number;
  name: string;
  path: string;
  kind: "file" | "iso" | "bluray" | "dvd";
  size: number;
  mtime: number;
  probe_status: string;
  probe_error?: string;
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
}
export interface Title {
  id: string;
  name: string;
  duration: number;
  chapters?: number;
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
    throw new Error(error.error || `Request failed (${response.status})`);
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

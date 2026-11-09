import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type Json = Record<string, any>;
type View = "queue" | "progress" | "processed" | "settings";
type ToastValue = { message: string; kind: "success" | "error" } | null;
type TrackDraft = { selected: boolean; language: string; name: string };
type SourceDraft = {
  titleId: string;
  preset: "preferred" | "minimal" | "all";
  tracks: Record<string, TrackDraft>;
  outputKind: "movie" | "television";
  directory: string;
  newDirectory: string;
  outputTitle: string;
  outputDate: string;
  filename: string;
};

const activeStatuses = new Set(["queued", "running", "awaiting_overwrite"]);
const inspectStatuses = new Set(["inspection_queued", "inspecting"]);

async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (typeof options.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let payload: Json = {};
    try {
      payload = await response.json();
    } catch {
      // Preserve the HTTP fallback below.
    }
    const error = new Error(
      payload.error || payload.detail || `Request failed (${response.status})`,
    ) as Error & { status?: number; payload?: Json };
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return response.status === 204 ? ({} as T) : response.json();
}

function label(value = "") {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function basename(path = "") {
  return path.split(/[\\/]/).pop() || path;
}

function withoutExtension(value = "") {
  return value.replace(/\.(mkv|mp4|m4v|mov|avi|mpg|mpeg|m2ts|mts|ts|iso)$/i, "");
}

function dateTime(value?: string | null) {
  if (!value) return "Not yet";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function trackKey(track: Json) {
  return String(track.key);
}

function selectedTitle(source: Json, id?: string) {
  const titles = source.metadata?.titles || [];
  return (
    titles.find((title: Json) => String(title.id) === String(id)) ||
    titles.find((title: Json) => title.suggested) ||
    titles[0] ||
    null
  );
}

function titleAndDate(source: Json) {
  const raw = withoutExtension(source.name || "");
  const match = raw.match(/(?:^|[\s[(.])((?:19|20)\d{2})(?:[\s)\].]|$)/);
  const date = match?.[1] || "";
  const title = raw
    .replace(/\[(?:19|20)\d{2}\]/g, "")
    .replace(/\((?:19|20)\d{2}\)/g, "")
    .replace(/\b(?:19|20)\d{2}\b/g, "")
    .replace(/[._]+/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
  return { title, date };
}

function suggestedFilename(source: Json) {
  if (source.series_name && source.episode_number !== null) {
    const season = String(source.season_number ?? 1).padStart(2, "0");
    const episode = String(source.episode_number).padStart(2, "0");
    const episodeTitle = source.episode_title ? ` - ${source.episode_title}` : "";
    return `${source.series_name} - S${season}E${episode}${episodeTitle}.mkv`;
  }
  return `${withoutExtension(source.name)}.mkv`;
}

function languageValue(track: Json) {
  const value = String(track.language || "").toLowerCase();
  return ["", "und", "unknown"].includes(value) ? "" : value;
}

function preferredTrackSelection(
  title: Json,
  preferences: Json,
  preset: SourceDraft["preset"],
) {
  const result: Record<string, TrackDraft> = {};
  const tracks = title?.tracks || [];
  const audio = tracks.filter((track: Json) => track.type === "audio");
  const subtitles = tracks.filter((track: Json) => track.type === "subtitle");
  const preferredAudio = preferences.preferred_audio_language || "";
  const preferredSubtitle = preferences.preferred_subtitle_language || "";
  const audioMatches = audio.filter(
    (track: Json) => languageValue(track) === preferredAudio,
  );
  const subtitleMatches = subtitles.filter(
    (track: Json) => languageValue(track) === preferredSubtitle,
  );
  const defaultAudio = audio.find((track: Json) => track.default) || audio[0];

  for (const track of tracks) {
    let selected = track.type === "video";
    if (preset === "all") selected = true;
    if (preset === "minimal" && track.type === "audio") {
      selected = trackKey(track) === trackKey(defaultAudio || {});
    }
    if (preset === "preferred" && track.type === "audio") {
      const choices = audioMatches.length ? audioMatches : defaultAudio ? [defaultAudio] : [];
      selected = choices.some((item: Json) => trackKey(item) === trackKey(track));
    }
    if (preset === "preferred" && track.type === "subtitle") {
      selected =
        subtitleMatches.some((item: Json) => trackKey(item) === trackKey(track)) &&
        (!preferences.forced_subtitles_only || track.forced);
    }
    result[trackKey(track)] = {
      selected,
      language: languageValue(track),
      name: track.name || "",
    };
  }
  return result;
}

function makeDraft(source: Json, preferences: Json): SourceDraft {
  const title = selectedTitle(source);
  const inferred = titleAndDate(source);
  return {
    titleId: String(title?.id ?? "0"),
    preset: "preferred",
    tracks: preferredTrackSelection(title, preferences, "preferred"),
    outputKind: source.series_name ? "television" : "movie",
    directory: "",
    newDirectory: "",
    outputTitle: source.series_name || inferred.title,
    outputDate: inferred.date,
    filename: suggestedFilename(source),
  };
}

function statusTone(status = "") {
  if (["complete", "ready", "kept"].includes(status)) return "success";
  if (["failed", "error", "retryable"].includes(status)) return "error";
  if (["running", "inspecting"].includes(status)) return "active";
  if (status === "awaiting_overwrite") return "warning";
  return "neutral";
}

function StatusPill({ status, text }: { status: string; text?: string }) {
  return (
    <span className={`status-pill ${statusTone(status)}`}>
      <i />
      {text || label(status)}
    </span>
  );
}

function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <i />
      <b />
    </span>
  );
}

function Toast({ value }: { value: ToastValue }) {
  return value ? (
    <div className={`toast ${value.kind}`} role="status">
      {value.message}
    </div>
  ) : null;
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2 id="modal-title">{title}</h2>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="modal-content">{children}</div>
      </section>
    </div>
  );
}

function SourceFacts({ source }: { source: Json }) {
  const summary = source.media_summary || {};
  return (
    <div className="source-facts">
      <StatusPill status={source.probe_status} />
      <span>{source.kind?.toUpperCase()}</span>
      <span>{source.size_display}</span>
      {summary.resolution && (
        <span>
          {summary.resolution} · <strong>{summary.resolution_class}</strong>
        </span>
      )}
      {summary.duration_display && <span>{summary.duration_display}</span>}
      {source.active_job_count > 0 && <span className="queued-note">In queue</span>}
    </div>
  );
}

function TrackRow({
  source,
  track,
  draft,
  languages,
  update,
  preview,
}: {
  source: Json;
  track: Json;
  draft: SourceDraft;
  languages: Json[];
  update: (key: string, value: Partial<TrackDraft>) => void;
  preview: (source: Json, track: Json, type: "audio" | "subtitle") => void;
}) {
  const key = trackKey(track);
  const value = draft.tracks[key] || {
    selected: true,
    language: languageValue(track),
    name: track.name || "",
  };
  const reported = track.language_name || track.language || "Undefined";
  const detail = [
    track.type === "video" && track.resolution,
    track.type === "video" && track.resolution_class,
    track.audio_channels && `${track.audio_channels} channels`,
    track.default && "Default",
    track.forced && "Forced",
  ].filter(Boolean);
  const editable = ["audio", "subtitle"].includes(track.type);
  return (
    <div className={`track-row ${value.selected ? "selected" : ""}`}>
      <label className="track-toggle" title={`Include ${track.type} track`}>
        <input
          type="checkbox"
          checked={value.selected}
          onChange={(event) => update(key, { selected: event.target.checked })}
        />
        <i />
      </label>
      <span className={`track-kind ${track.type}`}>{track.type}</span>
      <div className="track-main">
        <strong>{track.codec || "Unknown codec"}</strong>
        <span>{detail.join(" · ") || "No additional details"}</span>
      </div>
      {editable ? (
        <>
          <label className="compact-field">
            <span>Language</span>
            <select
              className={!value.language ? "needs-value" : ""}
              value={value.language}
              onChange={(event) => update(key, { language: event.target.value })}
            >
              <option value="">Undefined ({reported})</option>
              {languages.map((language) => (
                <option value={language.code} key={language.code}>
                  {language.name}
                </option>
              ))}
            </select>
          </label>
          <label className="compact-field track-name-field">
            <span>Track name</span>
            <input
              value={value.name}
              placeholder={track.name || "Optional name"}
              onChange={(event) => update(key, { name: event.target.value })}
            />
          </label>
        </>
      ) : (
        <div className="track-spacer">{reported}</div>
      )}
      {track.type === "audio" && source.kind !== "iso" && (
        <button
          className="button preview-button"
          type="button"
          onClick={() => preview(source, track, "audio")}
        >
          ▶ Sample
        </button>
      )}
      {track.type === "subtitle" && source.kind !== "iso" && (
        <button
          className="button preview-button"
          type="button"
          onClick={() => preview(source, track, "subtitle")}
        >
          Aa Sample
        </button>
      )}
    </div>
  );
}

function SourceConfiguration({
  source,
  settings,
  preferences,
  directories,
  getDirectories,
  onQueue,
  onPreview,
  onSeasonTemplate,
  notify,
}: {
  source: Json;
  settings: Json;
  preferences: Json;
  directories: Record<string, Json>;
  getDirectories: (kind: string) => Promise<void>;
  onQueue: (source: Json, draft: SourceDraft) => Promise<void>;
  onPreview: (source: Json, track: Json, type: "audio" | "subtitle", titleId: string) => void;
  onSeasonTemplate?: (source: Json, draft: SourceDraft) => Promise<void>;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [draft, setDraft] = useState<SourceDraft>(() => makeDraft(source, preferences));
  const [busy, setBusy] = useState(false);
  const [seasonBusy, setSeasonBusy] = useState(false);
  const title = selectedTitle(source, draft.titleId);
  const titles = source.metadata?.titles || [];
  const directoryData = directories[draft.outputKind];

  useEffect(() => {
    getDirectories(draft.outputKind).catch((error) => notify(error.message, "error"));
  }, [draft.outputKind, getDirectories, notify]);

  const setPreset = (preset: SourceDraft["preset"]) => {
    setDraft((current) => ({
      ...current,
      preset,
      tracks: preferredTrackSelection(title, preferences, preset),
    }));
  };

  const changeTitle = (titleId: string) => {
    const nextTitle = selectedTitle(source, titleId);
    setDraft((current) => ({
      ...current,
      titleId,
      tracks: preferredTrackSelection(nextTitle, preferences, current.preset),
    }));
  };

  const updateTrack = (key: string, value: Partial<TrackDraft>) => {
    setDraft((current) => ({
      ...current,
      tracks: {
        ...current.tracks,
        [key]: { ...current.tracks[key], ...value },
      },
    }));
  };

  const applyTitleDate = () => {
    const titlePart = draft.outputTitle.trim() || withoutExtension(source.name);
    const datePart = draft.outputDate.trim();
    setDraft((current) => ({
      ...current,
      filename: `${titlePart}${datePart ? ` (${datePart})` : ""}.mkv`,
    }));
  };

  const destinationRoot =
    draft.outputKind === "movie" ? settings.movies : settings.television;
  const relativeDirectory = [draft.directory, draft.newDirectory]
    .filter(Boolean)
    .join("/")
    .replace(/\/+/g, "/");
  const destination = `${destinationRoot}/${relativeDirectory ? `${relativeDirectory}/` : ""}${draft.filename}`;
  const selectedCount = Object.values(draft.tracks).filter((track) => track.selected).length;

  const queue = async () => {
    setBusy(true);
    try {
      await onQueue(source, draft);
    } finally {
      setBusy(false);
    }
  };

  const applySeasonTemplate = async () => {
    if (!onSeasonTemplate) return;
    setSeasonBusy(true);
    try {
      await onSeasonTemplate(source, draft);
    } finally {
      setSeasonBusy(false);
    }
  };

  return (
    <div className="source-configuration">
      <div className="configuration-toolbar">
        {titles.length > 1 && (
          <label className="field title-choice">
            <span>Feature or disc title</span>
            <select value={draft.titleId} onChange={(event) => changeTitle(event.target.value)}>
              {titles.map((item: Json) => (
                <option value={String(item.id)} key={String(item.id)}>
                  {item.name} · {item.duration_display} · {item.resolution_class}
                  {item.suggested ? " · suggested" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="choice-nav" role="group" aria-label="Track selection preset">
          {(["preferred", "minimal", "all"] as const).map((preset) => (
            <button
              type="button"
              key={preset}
              className={draft.preset === preset ? "selected" : ""}
              onClick={() => setPreset(preset)}
            >
              {preset === "preferred"
                ? "Preferred"
                : preset === "minimal"
                  ? "Minimal"
                  : "All tracks"}
            </button>
          ))}
        </div>
        <span className="configuration-facts">
          {title?.resolution || "Unknown"} · {title?.resolution_class || "Unknown"} ·{" "}
          {title?.duration_display || "Unknown runtime"} · {selectedCount} of{" "}
          {title?.tracks?.length || 0} tracks
        </span>
      </div>

      <section className="configuration-section">
        <div className="track-list">
          {(title?.tracks || []).map((track: Json) => (
            <TrackRow
              key={trackKey(track)}
              source={source}
              track={track}
              draft={draft}
              languages={settings.languages || []}
              update={updateTrack}
              preview={(item, selectedTrack, type) =>
                onPreview(item, selectedTrack, type, draft.titleId)
              }
            />
          ))}
        </div>
      </section>

      <section className="configuration-section output-section">
        <div className="naming-fields">
          <label className="field">
            <span>Title</span>
            <input
              value={draft.outputTitle}
              placeholder="Movie or episode title"
              onChange={(event) =>
                setDraft((current) => ({ ...current, outputTitle: event.target.value }))
              }
            />
          </label>
          <label className="field">
            <span>Date or year</span>
            <input
              value={draft.outputDate}
              placeholder="1984 or 1984-10-26"
              onChange={(event) =>
                setDraft((current) => ({ ...current, outputDate: event.target.value }))
              }
            />
          </label>
          <button className="button secondary apply-name" type="button" onClick={applyTitleDate}>
            Apply to filename
          </button>
        </div>
        <div className="destination-fields">
          <label className="field">
            <span>Library</span>
            <select
              value={draft.outputKind}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  outputKind: event.target.value as SourceDraft["outputKind"],
                  directory: "",
                }))
              }
            >
              <option value="movie">Movies</option>
              <option value="television">Television</option>
            </select>
          </label>
          <label className="field directory-field">
            <span>Existing directory</span>
            <select
              value={draft.directory}
              disabled={!directoryData}
              onChange={(event) =>
                setDraft((current) => ({ ...current, directory: event.target.value }))
              }
            >
              {!directoryData && <option>Loading library directories…</option>}
              {(directoryData?.directories || []).map((directory: string) => (
                <option value={directory} key={directory || "root"}>
                  {directory || "/ (library root)"}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>New subdirectory</span>
            <input
              value={draft.newDirectory}
              placeholder="Optional"
              onChange={(event) =>
                setDraft((current) => ({ ...current, newDirectory: event.target.value }))
              }
            />
          </label>
          <label className="field filename-field">
            <span>Output filename</span>
            <input
              value={draft.filename}
              onChange={(event) =>
                setDraft((current) => ({ ...current, filename: event.target.value }))
              }
            />
          </label>
        </div>
        <footer className="queue-confirmation">
          <div>
            <span>Confirmed destination</span>
            <code>{destination}</code>
          </div>
          <button
            className="button primary"
            type="button"
            disabled={busy || source.active_job_count > 0}
            onClick={queue}
          >
            {source.active_job_count > 0
              ? "Already scheduled"
              : busy
                ? "Scheduling…"
                : "Schedule remux"}
          </button>
          {onSeasonTemplate && (
            <button
              className="button secondary season-template-button"
              type="button"
              disabled={seasonBusy}
              onClick={applySeasonTemplate}
            >
              {seasonBusy ? "Checking season…" : "Apply as season template"}
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}

function SourceRow({
  source,
  open,
  detail,
  onToggle,
  onInspect,
  onDisposition,
  children,
}: {
  source: Json;
  open: boolean;
  detail?: Json;
  onToggle: () => void;
  onInspect: () => void;
  onDisposition: (source: Json, action: "defer" | "resume" | "trash" | "delete") => void;
  children?: React.ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [visited, setVisited] = useState(open);
  useEffect(() => {
    if (open) setVisited(true);
  }, [open]);
  const episode =
    source.episode_number !== null
      ? `E${String(source.episode_number).padStart(2, "0")} · `
      : "";
  const displayName = `${episode}${source.episode_title || source.name}`;
  return (
    <article className={`source-row ${open ? "expanded" : ""}`}>
      <div className="source-summary-wrap">
        <button
          className="source-summary"
          type="button"
          aria-expanded={open}
          onClick={onToggle}
        >
          <span className={`source-icon ${source.kind}`}>{source.kind === "iso" ? "◉" : "▶"}</span>
          <span className="source-name">
            <strong>{displayName}</strong>
            <code>{source.path}</code>
          </span>
          <SourceFacts source={source} />
          <span className="accordion-control">
            {open ? "Close" : "Review"} <b>⌄</b>
          </span>
        </button>
        <button
          className="source-more"
          type="button"
          aria-label={`Actions for ${displayName}`}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
        >
          •••
        </button>
        {menuOpen && (
          <div className="source-action-menu">
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onDisposition(source, source.queue_state === "deferred" ? "resume" : "defer");
              }}
            >
              {source.queue_state === "deferred" ? "Return to awaiting" : "Defer source"}
            </button>
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onDisposition(source, "trash");
              }}
            >
              Move to trash
            </button>
            <button
              className="danger"
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onDisposition(source, "delete");
              }}
            >
              Delete permanently
            </button>
          </div>
        )}
      </div>
      <div className="accordion-panel" aria-hidden={!open} inert={!open}>
        <div className="accordion-inner">
          {visited && (
            <>
              {inspectStatuses.has(source.probe_status) && (
                <div className="loading-state">
                  <i />
                  <div>
                    <strong>Inspecting media tracks</strong>
                    <span>The worker is reading this source in the background.</span>
                  </div>
                </div>
              )}
              {source.probe_status === "discovered" && (
                <div className="loading-state">
                  <div>
                    <strong>Track details have not been read</strong>
                    <span>Inspection runs only when requested and does not alter the source.</span>
                  </div>
                  <button className="button primary" type="button" onClick={onInspect}>
                    Inspect source
                  </button>
                </div>
              )}
              {source.probe_status === "error" && (
                <div className="error-state">
                  <div>
                    <strong>Inspection failed</strong>
                    <span>{source.probe_error}</span>
                  </div>
                  <button className="button secondary" type="button" onClick={onInspect}>
                    Retry inspection
                  </button>
                </div>
              )}
              {source.probe_status === "ready" && !detail && (
                <div className="loading-state">
                  <i />
                  <span>Loading track details…</span>
                </div>
              )}
              {detail && children}
            </>
          )}
        </div>
      </div>
    </article>
  );
}

function SeasonGroup({
  seriesName,
  seasonNumber,
  sources,
  analysis,
  openSources,
  details,
  toggleSource,
  inspectSource,
  dispositionSource,
  analyzeSeason,
  renderConfiguration,
}: {
  seriesName: string;
  seasonNumber: number;
  sources: Json[];
  analysis?: Json;
  openSources: Set<number>;
  details: Record<number, Json>;
  toggleSource: (source: Json) => void;
  inspectSource: (source: Json) => void;
  dispositionSource: (
    source: Json,
    action: "defer" | "resume" | "trash" | "delete",
  ) => void;
  analyzeSeason: (seriesName: string, seasonNumber: number) => void;
  renderConfiguration: (source: Json, detail: Json) => React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const status =
    analysis?.status === "uniform"
      ? `Uniform tracks · ${analysis.matching} of ${analysis.total}`
      : analysis?.status === "mixed"
        ? `Mixed tracks · ${analysis.matching} matching · ${analysis.exceptions} exceptions`
        : analysis?.inspected
          ? `Inspecting · ${analysis.inspected} of ${analysis.total}`
          : "Track layout unknown";
  const analyzing = analysis?.status === "analyzing";
  return (
    <section className={`season-group ${collapsed ? "collapsed" : ""}`}>
      <div className="season-header">
        <button type="button" onClick={() => setCollapsed((value) => !value)}>
          <b>{collapsed ? "+" : "−"}</b>
          <span>
            <strong>Season {String(seasonNumber).padStart(2, "0")}</strong>
            <small>{sources.length} episode{sources.length === 1 ? "" : "s"} · {status}</small>
          </span>
        </button>
        {analysis?.status !== "uniform" && (
          <button
            className="button ghost season-analyze"
            type="button"
            disabled={analyzing}
            onClick={() => analyzeSeason(seriesName, seasonNumber)}
          >
            {analyzing ? "Analyzing…" : analysis?.status === "mixed" ? "Reanalyze" : "Analyze season"}
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="source-list">
          {sources.map((source) => (
            <SourceRow
              key={source.id}
              source={source}
              open={openSources.has(source.id)}
              detail={details[source.id]}
              onToggle={() => toggleSource(source)}
              onInspect={() => inspectSource(source)}
              onDisposition={dispositionSource}
            >
              {details[source.id] && renderConfiguration(source, details[source.id])}
            </SourceRow>
          ))}
        </div>
      )}
    </section>
  );
}

function MediaGroup({
  name,
  sources,
  seasonAnalyses,
  openSources,
  details,
  toggleSource,
  inspectSource,
  dispositionSource,
  analyzeSeason,
  renderConfiguration,
}: {
  name: string;
  sources: Json[];
  seasonAnalyses: Json[];
  openSources: Set<number>;
  details: Record<number, Json>;
  toggleSource: (source: Json) => void;
  inspectSource: (source: Json) => void;
  dispositionSource: (
    source: Json,
    action: "defer" | "resume" | "trash" | "delete",
  ) => void;
  analyzeSeason: (seriesName: string, seasonNumber: number) => void;
  renderConfiguration: (source: Json, detail: Json) => React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const seasons = [...new Set(
    sources.map((source) => source.season_number).filter((value) => value !== null),
  )] as number[];
  const isSeries = name !== "Movies & other media" && seasons.length > 0;
  return (
    <section className={`media-group ${collapsed ? "collapsed" : ""}`}>
      <header className="group-header">
        <button type="button" onClick={() => setCollapsed((value) => !value)}>
          <span className="group-monogram">{name.slice(0, 1).toUpperCase()}</span>
          <span>
            <strong>{name}</strong>
            <small>
              {sources.length} source{sources.length === 1 ? "" : "s"}
              {seasons.length
                ? ` · ${seasons.length} season${seasons.length === 1 ? "" : "s"}`
                : ""}
            </small>
          </span>
          <b>{collapsed ? "+" : "−"}</b>
        </button>
      </header>
      {!collapsed && (
        isSeries ? (
          <div className="season-list">
            {seasons.sort((a, b) => a - b).map((seasonNumber) => (
              <SeasonGroup
                key={seasonNumber}
                seriesName={name}
                seasonNumber={seasonNumber}
                sources={sources.filter(
                  (source) => Number(source.season_number || 1) === seasonNumber,
                )}
                analysis={seasonAnalyses.find(
                  (item) =>
                    item.series_name === name &&
                    Number(item.season_number) === seasonNumber,
                )}
                openSources={openSources}
                details={details}
                toggleSource={toggleSource}
                inspectSource={inspectSource}
                dispositionSource={dispositionSource}
                analyzeSeason={analyzeSeason}
                renderConfiguration={renderConfiguration}
              />
            ))}
          </div>
        ) : (
          <div className="source-list">
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                open={openSources.has(source.id)}
                detail={details[source.id]}
                onToggle={() => toggleSource(source)}
                onInspect={() => inspectSource(source)}
                onDisposition={dispositionSource}
              >
                {details[source.id] && renderConfiguration(source, details[source.id])}
              </SourceRow>
            ))}
          </div>
        )
      )}
    </section>
  );
}

function QueueWorkspace({
  sources,
  seasonAnalyses,
  settings,
  preferences,
  directories,
  getDirectories,
  refreshSources,
  refreshSeasons,
  refreshJobs,
  notify,
  onPreview,
}: {
  sources: Json[];
  seasonAnalyses: Json[];
  settings: Json;
  preferences: Json;
  directories: Record<string, Json>;
  getDirectories: (kind: string) => Promise<void>;
  refreshSources: () => Promise<void>;
  refreshSeasons: () => Promise<void>;
  refreshJobs: () => Promise<void>;
  notify: (message: string, kind?: "success" | "error") => void;
  onPreview: (source: Json, track: Json, type: "audio" | "subtitle", titleId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [queueFilter, setQueueFilter] = useState<"awaiting" | "deferred" | "all">("awaiting");
  const [scanning, setScanning] = useState(false);
  const [openSources, setOpenSources] = useState<Set<number>>(new Set());
  const [details, setDetails] = useState<Record<number, Json>>({});
  const [seasonPlanState, setSeasonPlanState] = useState<Json | null>(null);
  const [seasonSelected, setSeasonSelected] = useState<Set<number>>(new Set());
  const [seasonScheduling, setSeasonScheduling] = useState(false);
  const inspecting = useRef<Set<number>>(new Set());

  const loadDetail = useCallback(async (id: number) => {
    const detail = await api(`/api/sources/${id}`);
    setDetails((current) => ({ ...current, [id]: detail }));
    return detail;
  }, []);

  const pollInspection = useCallback(
    async (source: Json) => {
      if (inspecting.current.has(source.id)) return;
      inspecting.current.add(source.id);
      try {
        for (let attempt = 0; attempt < 900; attempt += 1) {
          const detail = await api(`/api/sources/${source.id}`);
          if (detail.probe_status === "ready") {
            setDetails((current) => ({ ...current, [source.id]: detail }));
            await Promise.all([refreshSources(), refreshSeasons()]);
            return;
          }
          if (detail.probe_status === "error") {
            await Promise.all([refreshSources(), refreshSeasons()]);
            notify(detail.probe_error || "Inspection failed", "error");
            return;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 1200));
        }
      } finally {
        inspecting.current.delete(source.id);
      }
    },
    [notify, refreshSeasons, refreshSources],
  );

  const inspectSource = useCallback(
    async (source: Json) => {
      try {
        await api(`/api/sources/${source.id}/inspect`, { method: "POST" });
        await refreshSources();
        pollInspection(source);
      } catch (error) {
        notify((error as Error).message, "error");
      }
    },
    [notify, pollInspection, refreshSources],
  );

  const toggleSource = async (source: Json) => {
    const isOpen = openSources.has(source.id);
    setOpenSources(isOpen ? new Set<number>() : new Set<number>([source.id]));
    if (isOpen) return;
    if (source.probe_status === "ready" && !details[source.id]) {
      try {
        await loadDetail(source.id);
      } catch (error) {
        notify((error as Error).message, "error");
      }
    } else if (inspectStatuses.has(source.probe_status)) {
      pollInspection(source);
    }
  };

  const scan = async () => {
    setScanning(true);
    try {
      const result = await api("/api/scan", { method: "POST" });
      await Promise.all([refreshSources(), refreshSeasons()]);
      notify(
        result.warnings?.[0] ||
          `Inventory updated: ${result.found} sources, ${result.new} new`,
        result.warnings?.length ? "error" : "success",
      );
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setScanning(false);
    }
  };

  const dispositionSource = async (
    source: Json,
    action: "defer" | "resume" | "trash" | "delete",
  ) => {
    const destructive = action === "trash" || action === "delete";
    if (
      destructive &&
      !window.confirm(
        `${action === "trash" ? "Move this source to trash" : "Permanently delete this source"}?\n\n${source.path}`,
      )
    ) return;
    try {
      await api(`/api/sources/${source.id}/disposition`, {
        method: "POST",
        body: JSON.stringify({ action, confirm: destructive }),
      });
      notify(
        action === "defer"
          ? "Source deferred"
          : action === "resume"
            ? "Source returned to the queue"
            : action === "trash"
              ? "Source moved to trash"
              : "Source deleted",
      );
      await Promise.all([refreshSources(), refreshSeasons()]);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  const analyzeSeason = async (seriesName: string, seasonNumber: number) => {
    try {
      const result = await api("/api/seasons/analyze", {
        method: "POST",
        body: JSON.stringify({ series_name: seriesName, season_number: seasonNumber }),
      });
      notify(
        result.queued
          ? `Inspecting ${result.queued} season source${result.queued === 1 ? "" : "s"}`
          : "Season track analysis is current",
      );
      await Promise.all([refreshSources(), refreshSeasons()]);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  const queueSource = async (source: Json, draft: SourceDraft, overwrite = false) => {
    const title = selectedTitle(details[source.id], draft.titleId);
    const selectedTracks = Object.entries(draft.tracks)
      .filter(([, track]) => track.selected)
      .map(([key]) => key);
    const trackOverrides = Object.fromEntries(
      Object.entries(draft.tracks)
        .filter(([, track]) => track.language || track.name)
        .map(([key, track]) => [key, { language: track.language, name: track.name }]),
    );
    const directory = [draft.directory, draft.newDirectory].filter(Boolean).join("/");
    try {
      await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          source_id: source.id,
          title_id: String(title?.id),
          output_kind: draft.outputKind,
          relative_path: `${directory ? `${directory}/` : ""}${draft.filename}`,
          selected_tracks: selectedTracks,
          track_overrides: trackOverrides,
          overwrite,
        }),
      });
      notify("Remux scheduled");
      setOpenSources(new Set<number>());
      await Promise.all([refreshSources(), refreshSeasons(), refreshJobs()]);
    } catch (error) {
      const requestError = error as Error & { status?: number; payload?: Json };
      if (
        requestError.status === 409 &&
        requestError.payload?.code === "destination_exists" &&
        window.confirm(
          `This destination already exists. Overwrite it?\n\n${requestError.payload.output_path}`,
        )
      ) return queueSource(source, draft, true);
      notify(requestError.message, "error");
    }
  };

  const applySeasonTemplate = async (source: Json, draft: SourceDraft) => {
    const analysis = seasonAnalyses.find(
      (item) =>
        item.series_name === source.series_name &&
        Number(item.season_number) === Number(source.season_number || 1),
    );
    if (!analysis || analysis.inspected < analysis.total) {
      await analyzeSeason(source.series_name, Number(source.season_number || 1));
      notify("Season inspection must finish before the template can be applied");
      return;
    }
    const selectedTracks = Object.entries(draft.tracks)
      .filter(([, track]) => track.selected)
      .map(([key]) => key);
    const trackOverrides = Object.fromEntries(
      Object.entries(draft.tracks)
        .filter(([, track]) => track.language || track.name)
        .map(([key, track]) => [key, { language: track.language, name: track.name }]),
    );
    const relativeDirectory = [draft.directory, draft.newDirectory].filter(Boolean).join("/");
    const body = {
      series_name: source.series_name,
      season_number: Number(source.season_number || 1),
      template_source_id: source.id,
      title_id: draft.titleId,
      selected_tracks: selectedTracks,
      track_overrides: trackOverrides,
      output_kind: draft.outputKind,
      relative_directory: relativeDirectory,
    };
    try {
      const result = await api("/api/seasons/preflight", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setSeasonPlanState({ body, result });
      setSeasonSelected(
        new Set(
          result.items
            .filter((item: Json) => item.matched)
            .map((item: Json) => Number(item.source_id)),
        ),
      );
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  const scheduleSeason = async () => {
    if (!seasonPlanState || !seasonSelected.size) return;
    setSeasonScheduling(true);
    try {
      const result = await api("/api/seasons/schedule", {
        method: "POST",
        body: JSON.stringify({
          ...seasonPlanState.body,
          confirm: true,
          source_ids: [...seasonSelected],
        }),
      });
      notify(`${result.scheduled} season episodes scheduled`);
      setSeasonPlanState(null);
      setOpenSources(new Set<number>());
      await Promise.all([refreshSources(), refreshSeasons(), refreshJobs()]);
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setSeasonScheduling(false);
    }
  };

  const queueSources = useMemo(
    () =>
      sources.filter(
        (source) =>
          source.queue_state === "awaiting" ||
          source.queue_state === "deferred",
      ),
    [sources],
  );
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return queueSources.filter((source) => {
      if (queueFilter !== "all" && source.queue_state !== queueFilter) return false;
      if (!term) return true;
      return [source.name, source.path, source.series_name, source.episode_title, source.kind]
        .some((value) => String(value || "").toLowerCase().includes(term));
    });
  }, [queueFilter, queueSources, search]);

  const groups = useMemo(() => {
    const grouped = new Map<string, Json[]>();
    for (const source of filtered) {
      const group = source.series_name || "Movies & other media";
      grouped.set(group, [...(grouped.get(group) || []), source]);
    }
    return [...grouped.entries()].sort(([a], [b]) => {
      if (a === "Movies & other media") return 1;
      if (b === "Movies & other media") return -1;
      return a.localeCompare(b);
    });
  }, [filtered]);

  return (
    <div className="page queue-source-page">
      <div className="queue-toolbar">
        <label className="search-field">
          <span className="visually-hidden">Search queue</span>
          <input
            type="search"
            value={search}
            placeholder="Filter sources awaiting a decision"
            onChange={(event) => setSearch(event.target.value)}
          />
          <b>{filtered.length}</b>
        </label>
        <select
          aria-label="Queue state"
          value={queueFilter}
          onChange={(event) => setQueueFilter(event.target.value as typeof queueFilter)}
        >
          <option value="awaiting">Awaiting decision</option>
          <option value="deferred">Deferred</option>
          <option value="all">All queue sources</option>
        </select>
        <span className="queue-path"><code>{settings.ingest}</code></span>
        <button className="button primary" type="button" disabled={scanning} onClick={scan}>
          {scanning ? "Scanning…" : "Scan ingest"}
        </button>
      </div>

      <div className="media-groups">
        {groups.map(([name, groupSources]) => (
          <MediaGroup
            key={name}
            name={name}
            sources={groupSources}
            seasonAnalyses={seasonAnalyses}
            openSources={openSources}
            details={details}
            toggleSource={toggleSource}
            inspectSource={inspectSource}
            dispositionSource={dispositionSource}
            analyzeSeason={analyzeSeason}
            renderConfiguration={(summary, detail) => (
              <SourceConfiguration
                source={detail}
                settings={settings}
                preferences={preferences}
                directories={directories}
                getDirectories={getDirectories}
                onQueue={(_, draft) => queueSource(summary, draft)}
                onPreview={onPreview}
                onSeasonTemplate={
                  summary.series_name
                    ? (_, draft) => applySeasonTemplate(summary, draft)
                    : undefined
                }
                notify={notify}
              />
            )}
          />
        ))}
        {!groups.length && (
          <div className="empty-state">
            <span>✓</span>
            <h2>{queueSources.length ? "No sources match this view." : "The queue is clear."}</h2>
            <p>{queueSources.length ? "Try another filter." : "Run a scan when new media is ready."}</p>
          </div>
        )}
      </div>

      {seasonPlanState && (
        <Modal title="Season template preflight" onClose={() => setSeasonPlanState(null)}>
          <div className="season-preflight">
            <div className="season-preflight-summary">
              <strong>{seasonPlanState.result.matched} matching episodes</strong>
              <span>{seasonPlanState.result.exceptions} exceptions require individual review</span>
            </div>
            <div className="season-preflight-list">
              {seasonPlanState.result.items.map((item: Json) => (
                <label className={`season-preflight-item ${item.matched ? "" : "exception"}`} key={item.source_id}>
                  <input
                    type="checkbox"
                    disabled={!item.matched}
                    checked={item.matched && seasonSelected.has(Number(item.source_id))}
                    onChange={(event) =>
                      setSeasonSelected((current) => {
                        const next = new Set(current);
                        event.target.checked
                          ? next.add(Number(item.source_id))
                          : next.delete(Number(item.source_id));
                        return next;
                      })
                    }
                  />
                  <span>
                    <strong>
                      E{String(item.episode_number ?? "—").padStart(2, "0")} ·{" "}
                      {item.episode_title || item.name}
                    </strong>
                    <small>{item.matched ? item.relative_path : item.reason}</small>
                  </span>
                </label>
              ))}
            </div>
            <footer className="season-preflight-actions">
              <span>{seasonSelected.size} selected</span>
              <button
                className="button primary"
                type="button"
                disabled={!seasonSelected.size || seasonScheduling}
                onClick={scheduleSeason}
              >
                {seasonScheduling ? "Scheduling…" : `Schedule ${seasonSelected.size} episodes`}
              </button>
            </footer>
          </div>
        </Modal>
      )}
    </div>
  );
}

function JobLog({ id }: { id: number }) {
  const [log, setLog] = useState("Loading the latest worker output…");
  useEffect(() => {
    api(`/api/jobs/${id}/log`)
      .then((value) => setLog(value.log || "No worker output has been recorded yet."))
      .catch((error) => setLog(error.message));
  }, [id]);
  return <pre className="job-log">{log}</pre>;
}

function JobCard({
  job,
  act,
  notify,
}: {
  job: Json;
  act: (job: Json, action: string) => Promise<void>;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [open, setOpen] = useState(false);
  const running = job.status === "running";
  const canCancel = ["queued", "running"].includes(job.status);
  const canRetry = ["failed", "cancelled", "retryable"].includes(job.status);
  const overwrite = job.status === "awaiting_overwrite";
  return (
    <article className={`job-card ${running ? "live" : ""}`}>
      <div className="job-summary">
        <StatusPill status={job.status} />
        <div className="job-name">
          <strong>{job.source_name}</strong>
          <code>{job.output_path}</code>
        </div>
        <div className="job-phase">
          <span>{job.phase || (job.status === "queued" ? "Waiting for worker" : label(job.status))}</span>
          <strong>{Math.round(Number(job.progress || 0))}%</strong>
        </div>
        <time>{dateTime(job.started_at || job.created_at)}</time>
      </div>
      {(running || job.status === "complete") && (
        <div className="progress-track">
          <span style={{ width: `${Number(job.progress || 0)}%` }} />
        </div>
      )}
      {job.error && <div className="job-error">{job.error}</div>}
      <footer>
        <button className="button ghost" type="button" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide log" : "View log"}
        </button>
        <div>
          {canCancel && (
            <button className="button danger" type="button" onClick={() => act(job, "cancel")}>
              Cancel
            </button>
          )}
          {canRetry && (
            <button className="button secondary" type="button" onClick={() => act(job, "retry")}>
              Retry
            </button>
          )}
          {overwrite && (
            <button
              className="button danger"
              type="button"
              onClick={async () => {
                if (!window.confirm("Overwrite the existing destination file?")) return;
                try {
                  await act(job, "overwrite");
                } catch (error) {
                  notify((error as Error).message, "error");
                }
              }}
            >
              Approve overwrite
            </button>
          )}
        </div>
      </footer>
      {open && <JobLog id={job.id} />}
    </article>
  );
}

function Progress({
  jobs,
  refresh,
  notify,
}: {
  jobs: Json[];
  refresh: () => Promise<void>;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [filter, setFilter] = useState<"active" | "issues" | "all">("all");
  const act = async (job: Json, action: string) => {
    try {
      await api(`/api/jobs/${job.id}/${action}`, { method: "POST" });
      notify(
        action === "cancel"
          ? "Cancellation requested"
          : action === "retry"
            ? "Job returned to the queue"
            : "Overwrite approved",
        "success",
      );
      await refresh();
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };
  const unfinished = jobs.filter((job) => job.status !== "complete");
  const visible = unfinished.filter((job) => {
    if (filter === "active") return activeStatuses.has(job.status);
    if (filter === "issues") {
      return ["failed", "cancelled", "retryable", "awaiting_overwrite"].includes(job.status);
    }
    return true;
  });
  const running = jobs.filter((job) => job.status === "running").length;
  const waiting = jobs.filter((job) => job.status === "queued").length;
  return (
    <div className="page progress-page">
      <div className="progress-toolbar">
        <select
          value={filter}
          aria-label="Progress filter"
          onChange={(event) => setFilter(event.target.value as typeof filter)}
        >
          <option value="all">All unfinished jobs</option>
          <option value="active">Active and waiting</option>
          <option value="issues">Needs attention</option>
        </select>
        <span>{running} running · {waiting} waiting</span>
      </div>
      <div className="job-list">
        {visible.map((job) => (
            <JobCard key={job.id} job={job} act={act} notify={notify} />
        ))}
        {!visible.length && (
          <div className="empty-state compact">
            <span>✓</span>
            <h2>No jobs match this view.</h2>
            <p>Scheduled remuxes appear here immediately.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function Processed({
  rows,
  settings,
  refresh,
  notify,
}: {
  rows: Json[];
  settings: Json;
  refresh: () => Promise<void>;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const decide = async (
    item: Json,
    decision: "keep" | "later" | "trash" | "delete",
  ) => {
    const destructive = decision === "trash" || decision === "delete";
    if (
      destructive &&
      !window.confirm(
        `${decision === "trash" ? "Move this original source to trash" : "Permanently delete this original source"}?\n\n${item.source_path}\n\nThe verified MKV will be kept.`,
      )
    ) {
      return;
    }
    try {
      await api(`/api/cleanup/${item.id}`, {
        method: "POST",
        body: JSON.stringify({ decision, confirm: destructive }),
      });
      notify("Processed-source decision saved", "success");
      await refresh();
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };
  return (
    <div className="page processed-page">
      <div className="processed-toolbar">
        <span>{rows.length} verified output{rows.length === 1 ? "" : "s"} awaiting an original-source decision</span>
      </div>
      <div className="cleanup-list">
        {rows.map((item) => (
          <article className="cleanup-card" key={item.id}>
            <div className="cleanup-copy">
              <StatusPill status={item.status} text="Output verified" />
              <h2>{item.source_name}</h2>
              <dl>
                <div>
                  <dt>Original</dt>
                  <dd><code>{item.source_path}</code></dd>
                </div>
                <div>
                  <dt>MKV output</dt>
                  <dd><code>{item.output_path}</code></dd>
                </div>
              </dl>
              {item.error && <div className="job-error">{item.error}</div>}
            </div>
            <div className="cleanup-actions">
              <button className="button secondary" type="button" onClick={() => decide(item, "keep")}>
                Keep original
              </button>
              <button className="button ghost" type="button" onClick={() => decide(item, "later")}>
                Decide later
              </button>
              {settings.trash_enabled && (
                <button className="button secondary" type="button" onClick={() => decide(item, "trash")}>
                  Move to trash
                </button>
              )}
              <button className="button danger" type="button" onClick={() => decide(item, "delete")}>
                Delete permanently
              </button>
            </div>
          </article>
        ))}
        {!rows.length && (
          <div className="empty-state">
            <span>✓</span>
              <h2>No processed sources are waiting.</h2>
            <p>Verified remuxes will appear here after completion.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function SettingsPage({
  settings,
  preferences,
  onSaved,
  notify,
}: {
  settings: Json;
  preferences: Json;
  onSaved: (value: Json) => void;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [form, setForm] = useState(() => ({
    preferred_audio_language: preferences.preferred_audio_language || "",
    preferred_subtitle_language: preferences.preferred_subtitle_language || "",
    forced_subtitles_only: Boolean(preferences.forced_subtitles_only),
    ignored_patterns: (preferences.ignored_patterns || []).join("\n"),
  }));
  const [busy, setBusy] = useState(false);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api("/api/preferences", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          ignored_patterns: form.ignored_patterns.split("\n"),
        }),
      });
      onSaved(result);
      notify("Preferences saved", "success");
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page settings-page">
      <section className="settings-facts">
        <div><span>Ingest</span><code>{settings.ingest}</code></div>
        <div><span>Movies</span><code>{settings.movies}</code></div>
        <div><span>Television</span><code>{settings.television}</code></div>
      </section>
      <form className="preferences-form" onSubmit={save}>
        <header>
          <div>
            <h2>Track defaults</h2>
            <p>These choices are applied when a source is first opened.</p>
          </div>
        </header>
        <div className="preferences-fields">
          <label className="field">
            <span>Preferred audio language</span>
            <select
              value={form.preferred_audio_language}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  preferred_audio_language: event.target.value,
                }))
              }
            >
              <option value="">No preference</option>
              {(settings.languages || []).map((language: Json) => (
                <option value={language.code} key={language.code}>{language.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Preferred subtitle language</span>
            <select
              value={form.preferred_subtitle_language}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  preferred_subtitle_language: event.target.value,
                }))
              }
            >
              <option value="">No preference</option>
              {(settings.languages || []).map((language: Json) => (
                <option value={language.code} key={language.code}>{language.name}</option>
              ))}
            </select>
          </label>
          <label className="switch-field">
            <input
              type="checkbox"
              checked={form.forced_subtitles_only}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  forced_subtitles_only: event.target.checked,
                }))
              }
            />
            <i />
            <span>
              <strong>Prefer forced subtitles only</strong>
              <small>Still editable for every source</small>
            </span>
          </label>
        </div>
        <label className="field ignored-field">
          <span>Ignored folders and patterns</span>
          <textarea
            rows={6}
            value={form.ignored_patterns}
            placeholder={"One pattern per line\n*.sample.*\nExtras/*"}
            onChange={(event) =>
              setForm((current) => ({ ...current, ignored_patterns: event.target.value }))
            }
          />
          <small>Run another inventory scan after changing ignored patterns.</small>
        </label>
        <footer>
          <button className="button primary" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save preferences"}
          </button>
        </footer>
      </form>
      <section className="worker-facts">
        <header>
          <h2>Worker limits</h2>
          <p>Configured through the Docker Compose environment.</p>
        </header>
        <div>
          <span><strong>{settings.concurrency?.remux}</strong> heavy remux</span>
          <span><strong>{settings.concurrency?.probe}</strong> inspections</span>
          <span><strong>{settings.concurrency?.preview}</strong> previews</span>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>("queue");
  const [settings, setSettings] = useState<Json | null>(null);
  const [preferences, setPreferences] = useState<Json>({});
  const [sources, setSources] = useState<Json[]>([]);
  const [seasonAnalyses, setSeasonAnalyses] = useState<Json[]>([]);
  const [jobs, setJobs] = useState<Json[]>([]);
  const [cleanup, setCleanup] = useState<Json[]>([]);
  const [directories, setDirectories] = useState<Record<string, Json>>({});
  const [toast, setToast] = useState<ToastValue>(null);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Json | null>(null);
  const toastTimer = useRef<number | null>(null);

  const notify = useCallback((message: string, kind: "success" | "error" = "success") => {
    setToast({ message, kind });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4200);
  }, []);

  const refreshSources = useCallback(async () => {
    setSources(await api("/api/sources"));
  }, []);

  const refreshSeasons = useCallback(async () => {
    setSeasonAnalyses(await api("/api/seasons"));
  }, []);

  const refreshJobs = useCallback(async () => {
    setJobs(await api("/api/jobs"));
  }, []);

  const refreshCleanup = useCallback(async () => {
    setCleanup(await api("/api/cleanup"));
  }, []);

  const getDirectories = useCallback(async (kind: string) => {
    if (directories[kind]) return;
    const result = await api(`/api/directories?kind=${kind}`);
    setDirectories((current) => ({ ...current, [kind]: result }));
  }, [directories]);

  const createPreview = useCallback(
    async (
      source: Json,
      track: Json,
      type: "audio" | "subtitle",
      titleId: string,
    ) => {
      setPreview({
        type,
        title: type === "audio" ? "Audio sample" : "Subtitle sample",
        status: "loading",
        track,
      });
      try {
        const created = await api("/api/previews", {
          method: "POST",
          body: JSON.stringify({
            source_id: source.id,
            title_id: titleId,
            track_key: trackKey(track),
            preview_type: type,
          }),
        });
        for (let attempt = 0; attempt < 240; attempt += 1) {
          const result = await api(`/api/previews/${created.id}`);
          if (result.status === "complete") {
            setPreview({
              type,
              title: type === "audio" ? "Audio sample" : "Subtitle sample",
              status: "complete",
              result,
              track,
            });
            return;
          }
          if (result.status === "failed") throw new Error(result.error || "Preview failed");
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
        throw new Error("Preview generation timed out");
      } catch (previewError) {
        setPreview((current) => ({
          ...current,
          status: "error",
          error: (previewError as Error).message,
        }));
      }
    },
    [],
  );

  useEffect(() => {
    Promise.all([
      api("/api/settings"),
      api("/api/preferences"),
      api("/api/sources"),
      api("/api/seasons"),
      api("/api/jobs"),
      api("/api/cleanup"),
    ])
      .then(([
        nextSettings,
        nextPreferences,
        nextSources,
        nextSeasonAnalyses,
        nextJobs,
        nextCleanup,
      ]) => {
        setSettings(nextSettings);
        setPreferences(nextPreferences);
        setSources(nextSources);
        setSeasonAnalyses(nextSeasonAnalyses);
        setJobs(nextJobs);
        setCleanup(nextCleanup);
      })
      .catch((bootError) => setError(bootError.message));
  }, []);

  useEffect(() => {
    if (!settings) return;
    const fast = window.setInterval(() => {
      Promise.all([refreshJobs(), refreshCleanup()]).catch(() => undefined);
    }, 2500);
    const slow = window.setInterval(() => {
      Promise.all([refreshSources(), refreshSeasons()]).catch(() => undefined);
    }, 15000);
    return () => {
      window.clearInterval(fast);
      window.clearInterval(slow);
    };
  }, [refreshCleanup, refreshJobs, refreshSeasons, refreshSources, settings]);

  const queuedSources = sources.filter((source) =>
    ["awaiting", "deferred"].includes(source.queue_state),
  ).length;
  const progressJobs = jobs.filter((job) => job.status !== "complete").length;
  const serviceState = jobs.some((job) => job.status === "running") ? "busy" : "ready";

  if (error) {
    return (
      <main className="fatal-state">
        <BrandMark />
        <h1>Inosculate could not start.</h1>
        <p>{error}</p>
        <button className="button primary" onClick={() => window.location.reload()}>
          Try again
        </button>
      </main>
    );
  }

  if (!settings) {
    return (
      <main className="boot-state">
        <BrandMark />
        <span>Preparing media workspace…</span>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <button className="brand" type="button" onClick={() => setView("queue")}>
          <BrandMark />
          <strong>Inosculate</strong>
        </button>
        <nav aria-label="Application sections">
          {([
            ["queue", "Queue", queuedSources],
            ["progress", "Progress", progressJobs],
            ["processed", "Processed", cleanup.length],
            ["settings", "Settings", 0],
          ] as [View, string, number][]).map(([id, title, count]) => (
            <button
              type="button"
              key={id}
              className={view === id ? "active" : ""}
              onClick={() => setView(id)}
            >
              {title}
              {count > 0 && <b>{count}</b>}
            </button>
          ))}
        </nav>
        <div className={`service-state ${serviceState}`}>
          <i />
          <span>{serviceState === "busy" ? "Worker active" : "System ready"}</span>
        </div>
      </header>

      <main className="app-content">
        {view === "queue" && (
          <QueueWorkspace
            sources={sources}
            seasonAnalyses={seasonAnalyses}
            settings={settings}
            preferences={preferences}
            directories={directories}
            getDirectories={getDirectories}
            refreshSources={refreshSources}
            refreshSeasons={refreshSeasons}
            refreshJobs={refreshJobs}
            notify={notify}
            onPreview={createPreview}
          />
        )}
        {view === "progress" && (
          <Progress jobs={jobs} refresh={refreshJobs} notify={notify} />
        )}
        {view === "processed" && (
          <Processed
            rows={cleanup}
            settings={settings}
            refresh={refreshCleanup}
            notify={notify}
          />
        )}
        {view === "settings" && (
          <SettingsPage
            settings={settings}
            preferences={preferences}
            onSaved={setPreferences}
            notify={notify}
          />
        )}
      </main>

      <Toast value={toast} />
      {preview && (
        <Modal title={preview.title} onClose={() => setPreview(null)}>
          {preview.status === "loading" && (
            <div className="preview-loading">
              <i />
              <strong>
                {preview.type === "audio"
                  ? "Creating a one-minute sample near the middle…"
                  : "Extracting a subtitle sample…"}
              </strong>
              <span>The worker is handling this without blocking the interface.</span>
            </div>
          )}
          {preview.status === "error" && (
            <div className="error-state">
              <strong>Preview could not be created</strong>
              <span>{preview.error}</span>
            </div>
          )}
          {preview.status === "complete" && preview.type === "audio" && (
            <div className="audio-preview">
              <p>
                {preview.track.codec || "Audio"} ·{" "}
                {preview.track.language_name || preview.track.language || "Undefined language"}
              </p>
              <audio controls autoPlay src={preview.result.audio_url}>
                Your browser does not support audio playback.
              </audio>
            </div>
          )}
          {preview.status === "complete" && preview.type === "subtitle" && (
            <div className="subtitle-preview">
              {preview.result.result?.kind === "text" &&
                (preview.result.result.snippets || []).map((cue: Json, index: number) => (
                  <blockquote key={`${cue.time}-${index}`}>
                    <time>{cue.time}</time>
                    <p>{cue.text}</p>
                  </blockquote>
                ))}
              {preview.result.result?.kind === "image" && (
                <>
                  <p>{preview.result.result.message}</p>
                  <img src={preview.result.image_url} alt="Subtitle preview frame" />
                </>
              )}
              {!["text", "image"].includes(preview.result.result?.kind) && (
                <p>{preview.result.result?.message || "No subtitle sample was available."}</p>
              )}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

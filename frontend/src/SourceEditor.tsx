import { useEffect, useState } from "react";
import {
  api,
  Detail,
  Draft,
  draftFor,
  draftKey,
  duration,
  jobRequest,
  Preferences,
  presetKeys,
  RequestError,
  Settings,
  Title,
} from "./model";
import TrackEditor from "./TrackEditor";
import SeasonTemplate from "./SeasonTemplate";

interface Props {
  source: Detail;
  settings: Settings;
  prefs: Preferences;
  staged: Draft[];
  remembered: Map<string, Draft>;
  stage: (draft: Draft) => void;
  scheduled: (draft: Draft, seasonSources?: number[]) => void;
  notify: (message: string) => void;
}

export default function SourceEditor(props: Props) {
  const { source } = props;
  const titles = source.metadata.titles || [];
  const [id, choose] = useState(source.kind === "file" ? titles[0]?.id || "" : "");
  const [full, setFull] = useState<Title | null>(null);
  const [error, setError] = useState("");
  const [attempt, retry] = useState(0);
  const listed = titles.find((t) => t.id === id);
  useEffect(() => {
    let active = true;
    setFull(null);
    setError("");
    if (listed && !listed.tracks) {
      api<Title>(`sources/${source.id}/titles/${id}`, {})
        .then((t) => {
          if (active) setFull(t);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    }
    return () => {
      active = false;
    };
  }, [source.id, id, listed, attempt]);
  const title = listed?.tracks ? listed : full?.id === id ? full : null;
  return (
    <>
      {source.kind !== "file" && (
        <label className="field">
          Disc title or playlist
          <select value={id} onChange={(e) => choose(e.target.value)}>
            <option value="">Choose a title to inspect</option>
            {titles.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · {duration(t.duration)} · {t.chapters ?? 0} chapters
              </option>
            ))}
          </select>
        </label>
      )}
      {error && (
        <p className="alert" role="alert">
          {error} <button onClick={() => retry((n) => n + 1)}>Retry</button>
        </p>
      )}
      {id && !title && !error && <p role="status">Reading tracks…</p>}
      {title && <TitleEditor key={id} {...props} title={title} />}
    </>
  );
}

function TitleEditor({
  source,
  title,
  settings,
  prefs,
  staged,
  remembered,
  stage,
  scheduled,
  notify,
}: Props & { title: Title }) {
  const key = draftKey(source, title);
  const existing = staged.find(
    (d) => draftKey(d.source, d.title) === key && d.source.mtime === source.mtime,
  );
  const [draft, setDraft] = useState<Draft>(() => {
    const prior = existing || remembered.get(key);
    if (prior?.source.mtime === source.mtime) return prior;
    const { metadata: _metadata, ...summary } = source;
    const value = draftFor(summary, title);
    if (source.kind === "file" && source.series_name && source.episode_number != null) {
      const season = String(source.season_number ?? 1).padStart(2, "0");
      const episode = String(source.episode_number).padStart(2, "0");
      value.filename = `${source.series_name}/Season ${season}/${source.series_name} - S${season}E${episode}${source.episode_title ? " - " + source.episode_title : ""}.mkv`;
    }
    return { ...value, keys: presetKeys(title, prefs, "Preferred") };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [season, setSeason] = useState(false);
  function change(patch: Partial<Draft>) {
    const next = { ...draft, ...patch };
    remembered.set(key, next);
    setDraft(next);
    if (existing) stage(next);
  }
  async function queue() {
    setBusy(true);
    setError("");
    try {
      const request = jobRequest(draft);
      try {
        await api("jobs", request);
      } catch (e) {
        if (!(e instanceof RequestError) || e.code !== "destination_exists") throw e;
        if (!window.confirm(`Replace the existing output?\n\n${e.output}`)) return;
        await api("jobs", { ...request, overwrite: true });
      }
      scheduled(draft);
      notify(`Scheduled ${draft.filename}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const muxable = title.tracks?.some(
    (t) => ["video", "audio"].includes(t.type) && draft.keys.includes(t.key),
  );
  return (
    <fieldset className="source-editor" disabled={busy}>
      <div className="editor-head">
        <h3>Tracks</h3>
        <span>
          {[title.resolution, title.resolution_class, duration(title.duration)]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
      <div className="actions" aria-label="Track selection presets">
        {(["Preferred", "Minimal", "All tracks"] as const).map((p) => (
          <button key={p} onClick={() => change({ keys: presetKeys(title, prefs, p) })}>
            {p}
          </button>
        ))}
        <span>{draft.keys.length} selected</span>
      </div>
      <TrackEditor draft={draft} settings={settings} change={change} />
      <Destination draft={draft} settings={settings} change={change} />
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button
          className="primary"
          disabled={!muxable || !draft.filename.split("/").pop()?.trim()}
          onClick={queue}
        >
          {busy ? "Scheduling…" : "Schedule remux"}
        </button>
        <button
          disabled={!muxable || !!existing}
          onClick={() => {
            stage(draft);
            notify(`Added ${draft.filename} to Batch`);
          }}
        >
          {existing ? "In batch" : "Add to batch"}
        </button>
        {source.kind === "file" && source.series_name && (
          <button disabled={!muxable} onClick={() => setSeason(true)}>
            Apply as season template
          </button>
        )}
      </div>
      {season && (
        <SeasonTemplate
          draft={draft}
          close={() => setSeason(false)}
          notify={notify}
          onScheduled={scheduled}
        />
      )}
    </fieldset>
  );
}

function Destination({
  draft,
  settings,
  change,
}: {
  draft: Draft;
  settings: Settings;
  change: (patch: Partial<Draft>) => void;
}) {
  const parts = draft.filename.split("/");
  const file = parts.pop() || "";
  const base = parts.join("/");
  const [dir, setDir] = useState(base);
  const [sub, setSub] = useState("");
  const year = draft.source.name.match(/\b(?:19|20)\d{2}\b/)?.[0] || "";
  const [name, setName] = useState(
    draft.source.name
      .replace(year, "")
      .replace(/[._]+/g, " ")
      .replace(/[()\[\]]/g, "")
      .trim(),
  );
  const [date, setDate] = useState(year);
  const [dirs, setDirs] = useState<string[]>([]);
  const [warning, setWarning] = useState("");
  useEffect(() => {
    let active = true;
    setDirs([]);
    setWarning("");
    api<{ directories: string[]; warnings: string[] }>(`directories?kind=${draft.library}`)
      .then((r) => {
        if (active) {
          setDirs(r.directories);
          setWarning(r.warnings.join(" "));
        }
      })
      .catch((e) => {
        if (active) setWarning(e.message);
      });
    return () => {
      active = false;
    };
  }, [draft.library]);
  const filename = (d: string, s: string, f: string) => [...[d, s].filter(Boolean), f].join("/");
  return (
    <section className="destination-editor">
      <h3>Destination</h3>
      <div className="editor-fields">
        <label>
          Title
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Date or year
          <input value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <button
          onClick={() =>
            change({
              filename: filename(
                dir,
                sub,
                `${name.trim() || draft.source.name}${date.trim() ? " (" + date.trim() + ")" : ""}.mkv`,
              ),
            })
          }
        >
          Apply to filename
        </button>
      </div>
      <div className="editor-fields">
        <label>
          Output library
          <select
            value={draft.library}
            onChange={(e) => {
              setDir("");
              setSub("");
              change({ library: e.target.value, filename: file });
            }}
          >
            <option value="movie">Movies</option>
            <option value="television">Television</option>
          </select>
        </label>
        <label>
          Existing directory
          <select
            value={dir}
            onChange={(e) => {
              setDir(e.target.value);
              change({ filename: filename(e.target.value, sub, file) });
            }}
          >
            {Array.from(new Set(["", dir, ...dirs])).map((d) => (
              <option key={d} value={d}>
                {d || "/ (library root)"}
              </option>
            ))}
          </select>
        </label>
        <label>
          New subdirectory
          <input
            value={sub}
            onChange={(e) => {
              setSub(e.target.value);
              change({ filename: filename(dir, e.target.value, file) });
            }}
          />
        </label>
        <label>
          Output filename
          <input
            value={file}
            onChange={(e) => change({ filename: filename(dir, sub, e.target.value) })}
          />
        </label>
      </div>
      {warning && <p role="status">{warning}</p>}
      <p>
        Confirmed destination{" "}
        <code>
          {draft.library === "movie" ? settings.movies : settings.television}/{draft.filename}
        </code>
      </p>
    </section>
  );
}

import { useEffect, useState } from "react";
import SourceEditor from "./SourceEditor";
import {
  api,
  Detail,
  Draft,
  draftFor,
  draftKey,
  formats,
  Preferences,
  Settings,
  Source,
} from "./model";

const states: Record<string, string> = {
  discovered: "Uninspected",
  ready: "Ready",
  inspecting: "Inspecting",
  inspection_queued: "Waiting",
  error: "Needs attention",
};
interface Props {
  sources: Source[];
  drafts: Draft[];
  add: (items: Draft[]) => void;
  reload: () => Promise<void>;
  review: () => void;
  settings: Settings;
  prefs: Preferences;
  stage: (draft: Draft) => void;
  scheduled: (draft: Draft, seasonSources?: number[]) => void;
  remembered: Map<string, Draft>;
}

export default function Library({
  sources,
  drafts,
  add,
  reload,
  review,
  settings,
  prefs,
  stage,
  scheduled,
  remembered,
}: Props) {
  const [state, setState] = useState("available");
  const [analyses, setAnalyses] = useState<
    {
      series_name: string;
      season_number: number;
      status: string;
      inspected: number;
      total: number;
      matching: number;
      exceptions: number;
    }[]
  >([]);
  const seasonStatus = sources
    .filter((s) => s.series_name && s.kind === "file")
    .map((s) => [s.id, s.probe_status, s.queue_state].join("/"))
    .join(",");
  useEffect(() => {
    let active = true;
    api<typeof analyses>("seasons")
      .then((a) => {
        if (active) setAnalyses(a);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [seasonStatus]);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [marked, setMarked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const current = sources.find((s) => s.id === focus);
  const visible = sources.filter(
    (s) =>
      (state === "all" ||
        (state === "available" ? s.queue_state !== "deferred" : s.queue_state === state)) &&
      (kind === "all" || (kind === "disc") === (s.kind !== "file")) &&
      (s.name.toLowerCase().includes(query.toLowerCase()) ||
        s.path.toLowerCase().includes(query.toLowerCase())),
  );
  const selectedFiles = sources.filter((s) => s.kind === "file" && marked.includes(s.id));
  const unread = selectedFiles.filter((s) => s.probe_status !== "ready");
  const visibleFiles = visible.filter((s) => s.kind === "file");
  const inspectable = visible.filter((s) => ["discovered", "error"].includes(s.probe_status));
  const currentId = current?.id,
    status = current?.probe_status,
    mtime = current?.mtime;

  useEffect(() => {
    let live = true;
    setDetail(null);
    if (currentId && status === "ready") {
      api<Detail>(`sources/${currentId}`)
        .then((value) => {
          if (live) setDetail(value);
        })
        .catch((e) => {
          if (live) setError(e.message);
        });
    }
    return () => {
      live = false;
    };
  }, [currentId, status, mtime]);

  async function run(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await operation();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function inspect(items: Source[]) {
    const result = await api<{ queued: number }>("inspect", { source_ids: items.map((s) => s.id) });
    setMessage(`Queued ${result.queued} sources for inspection.`);
    await reload();
  }
  async function scan() {
    const result = await api<{ found: number; warnings: string[] }>("scan", {});
    setMessage([`Found ${result.found} sources.`, ...result.warnings].join(" "));
    await reload();
  }
  async function bulkFiles() {
    if (unread.length) {
      await inspect(unread);
      return;
    }
    const files = await api<Detail[]>("files/review", {
      source_ids: selectedFiles.map((s) => s.id),
    });
    const results = files.map((file) => {
      const title = file.metadata.titles?.[0];
      if (!title?.tracks) throw new Error(`Inspect ${file.name} before adding it.`);
      const { metadata: _catalog, ...source } = file;
      const prior = remembered.get(draftKey(source, title));
      return prior?.source.mtime === source.mtime ? prior : draftFor(source, title);
    });
    add(results);
    setMarked([]);
    review();
  }
  function toggleVisible() {
    const ids = visibleFiles.map((s) => s.id);
    if (ids.every((id) => marked.includes(id))) setMarked(marked.filter((id) => !ids.includes(id)));
    else setMarked([...new Set([...marked, ...ids])]);
  }
  const groups = Array.from(
    visible
      .reduce((map, s) => {
        const series = s.kind === "file" ? s.series_name : undefined;
        const season = s.season_number ?? 1;
        const key = series ? JSON.stringify([series, season]) : "others";
        if (!map.has(key)) map.set(key, { key, series, season, items: [] });
        map.get(key)!.items.push(s);
        return map;
      }, new Map<string, { key: string; series?: string; season: number; items: Source[] }>())
      .values(),
  );
  function renderSource(s: Source) {
    const open = focus === s.id;
    return (
      <li key={s.id} className={"library-row" + (open ? " focused" : "")}>
        <div className="source-bar">
          {s.kind === "file" && (
            <input
              type="checkbox"
              aria-label={"Select " + s.name}
              checked={marked.includes(s.id)}
              onChange={(e) =>
                setMarked(e.target.checked ? [...marked, s.id] : marked.filter((id) => id !== s.id))
              }
            />
          )}
          <button
            className="source"
            aria-expanded={open}
            onClick={() => {
              setFocus(open ? null : s.id);
              setError("");
              if (!open && s.probe_status === "discovered" && !busy) run(() => inspect([s]));
            }}
          >
            <span aria-hidden="true" className={s.kind === "file" ? "file-icon" : "disc"} />
            <span className="source-name">
              <strong>{s.name}</strong>
              <small>
                {formats[s.kind]} · {s.path}
              </small>
            </span>
            <span className={"status " + s.probe_status}>
              {states[s.probe_status] || s.probe_status}
            </span>
            <span>{open ? "Close" : "Review"}</span>
          </button>
        </div>
        {open && (
          <section className="inline-review" aria-label="Source review">
            {s.probe_status === "ready" && detail?.id === s.id ? (
              <SourceEditor
                key={s.id + "/" + s.mtime}
                source={detail}
                settings={settings}
                prefs={prefs}
                staged={drafts}
                remembered={remembered}
                stage={stage}
                scheduled={scheduled}
                notify={setMessage}
              />
            ) : s.probe_status === "error" || s.probe_status === "discovered" ? (
              <>
                {s.probe_error && <p className="alert">{s.probe_error}</p>}
                <button onClick={() => run(() => inspect([s]))}>Inspect source</button>
              </>
            ) : (
              <p role="status">
                {s.probe_status === "ready" ? "Loading track details…" : "Inspecting source…"}
              </p>
            )}
            {["awaiting", "deferred"].includes(s.queue_state) && (
              <details>
                <summary>Original source actions</summary>
                <div className="actions">
                  {(s.queue_state === "deferred" ? ["resume"] : ["defer"])
                    .concat(settings.trash_enabled ? ["trash", "delete"] : ["delete"])
                    .map((action) => (
                      <button
                        key={action}
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            const destructive = action === "delete" || action === "trash";
                            if (
                              destructive &&
                              !window.confirm(`${action === "trash" ? "Move to trash" : "Permanently delete"}?

${s.path}`)
                            )
                              return;
                            await api(`sources/${s.id}/disposition`, {
                              action,
                              confirm: destructive,
                            });
                            await reload();
                            setMessage(`Source decision saved for ${s.name}`);
                          })
                        }
                      >
                        {
                          (
                            {
                              resume: "Return to awaiting",
                              defer: "Defer source",
                              trash: "Move to trash",
                              delete: "Delete permanently",
                            } as Record<string, string>
                          )[action]
                        }
                      </button>
                    ))}
                </div>
              </details>
            )}
          </section>
        )}
      </li>
    );
  }
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Source library</h1>
          <p>Choose disc titles or media files to remux.</p>
        </div>
        <button className="primary" disabled={busy} onClick={() => run(scan)}>
          Scan ingest folder
        </button>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      <div className="tools">
        <div className="segmented" aria-label="Source type">
          {[
            ["all", "All sources"],
            ["disc", "Discs"],
            ["file", "Files"],
          ].map(([k, label]) => (
            <button
              aria-pressed={kind === k}
              className={kind === k ? "selected" : ""}
              key={k}
              onClick={() => setKind(k)}
            >
              {label}
            </button>
          ))}
        </div>
        <select aria-label="Queue state" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="available">Available sources</option>
          <option value="awaiting">Awaiting decision</option>
          <option value="deferred">Deferred</option>
          <option value="all">All states</option>
        </select>
        <input
          type="search"
          aria-label="Search sources"
          placeholder="Search name or path"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="workspace">
        <div className="inventory">
          <div className="list-head">
            <span>
              {visible.length} {visible.length === 1 ? "source" : "sources"}
            </span>
            {inspectable.length > 0 && (
              <button
                className="quiet"
                disabled={busy}
                onClick={() => run(() => inspect(inspectable))}
              >
                Inspect visible ({inspectable.length})
              </button>
            )}
          </div>
          {visibleFiles.length > 0 && (
            <div className="bulk">
              <label>
                <input
                  type="checkbox"
                  aria-label="Select visible files"
                  checked={visibleFiles.every((s) => marked.includes(s.id))}
                  onChange={toggleVisible}
                />{" "}
                Select visible files
              </label>
              {selectedFiles.length > 0 && (
                <button
                  disabled={
                    busy ||
                    unread.some((s) => ["inspecting", "inspection_queued"].includes(s.probe_status))
                  }
                  onClick={() => run(bulkFiles)}
                >
                  {unread.length ? "Inspect" : "Review"} {selectedFiles.length} files
                </button>
              )}
            </div>
          )}
          {!visible.length && (
            <div className="empty">
              <div className="disc large" aria-hidden="true" />
              <h2>{sources.length ? "No matching sources" : "No sources yet"}</h2>
              <p>
                {sources.length
                  ? "Try a different filter or search."
                  : "Scan your ingest folder to find disc backups and media files."}
              </p>
            </div>
          )}
          <ul className="source-list">
            {groups.map((group) =>
              group.series ? (
                <li className="series-group" key={group.key}>
                  <details open>
                    <summary>
                      {group.series} · Season {group.season} ({group.items.length})
                    </summary>
                    {(() => {
                      const analysis = analyses.find(
                        (a) => a.series_name === group.series && a.season_number === group.season,
                      );
                      return (
                        <div className="season-facts">
                          <small>
                            {analysis?.status === "mixed"
                              ? `${analysis.matching} matching · ${analysis.exceptions} exceptions`
                              : analysis?.status === "uniform"
                                ? "Uniform track layout"
                                : `${analysis?.inspected || 0} inspected`}
                          </small>
                          <button
                            disabled={busy || !analysis}
                            onClick={() =>
                              run(async () => {
                                const r = await api<{ queued: number }>("seasons/analyze", {
                                  series_name: group.series,
                                  season_number: group.season,
                                });
                                setMessage(`Season analysis: ${r.queued} inspections queued.`);
                                await reload();
                              })
                            }
                          >
                            Analyze season
                          </button>
                        </div>
                      );
                    })()}
                    <ul className="source-list">{group.items.map(renderSource)}</ul>
                  </details>
                </li>
              ) : (
                group.items.map(renderSource)
              ),
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}

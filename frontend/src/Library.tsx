import { useEffect, useState } from "react";
import { api, Detail, Draft, draftFor, draftKey, duration, formats, Source, Title } from "./model";

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
}

export default function Library({ sources, drafts, add, reload, review }: Props) {
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
  async function addTitle(source: Source, title: Title) {
    const full = title.tracks
      ? title
      : await api<Title>(`sources/${source.id}/titles/${title.id}`, {});
    add([draftFor(source, full)]);
    setMessage(`${title.name} added to the batch.`);
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
      return draftFor(source, title);
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
        <input
          type="search"
          aria-label="Search sources"
          placeholder="Search name or path"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className={"workspace" + (current ? " split" : "")}>
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
            {visible.map((s) => (
              <li className={focus === s.id ? "focused" : ""} key={s.id}>
                {s.kind === "file" && (
                  <input
                    type="checkbox"
                    aria-label={"Select " + s.name}
                    checked={marked.includes(s.id)}
                    onChange={(e) =>
                      setMarked(
                        e.target.checked ? [...marked, s.id] : marked.filter((id) => id !== s.id),
                      )
                    }
                  />
                )}
                <button
                  className="source"
                  aria-expanded={focus === s.id}
                  onClick={() => {
                    setFocus(s.id);
                    setError("");
                    if (s.probe_status === "discovered" && !busy) run(() => inspect([s]));
                  }}
                >
                  <span aria-hidden="true" className={s.kind === "file" ? "file-icon" : "disc"} />
                  <span className="source-name">
                    <strong>{s.name}</strong>
                    <small>{formats[s.kind]}</small>
                  </span>
                  <span className={"status " + s.probe_status}>
                    {states[s.probe_status] || s.probe_status}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        {current && (
          <aside className="inspector" aria-label="Source details">
            <header>
              <small>{formats[current.kind]}</small>
              <button className="quiet" onClick={() => setFocus(null)}>
                Close
              </button>
            </header>
            <h2>{current.name}</h2>
            <p className="path">{current.path}</p>
            {current.probe_status === "error" ? (
              <>
                <p className="alert">{current.probe_error}</p>
                <button disabled={busy} onClick={() => run(() => inspect([current]))}>
                  Retry inspection
                </button>
              </>
            ) : current.probe_status !== "ready" ? (
              <p role="status">
                {states[current.probe_status]}.{" "}
                {current.probe_status === "discovered" && (
                  <button onClick={() => run(() => inspect([current]))}>Inspect source</button>
                )}
              </p>
            ) : !detail ? (
              <p>Loading titles…</p>
            ) : (
              <>
                <h3>{current.kind === "file" ? "Media file" : "Disc titles"}</h3>
                {current.kind !== "file" && <p>Choose each title you want to keep.</p>}
                <div className="titles">
                  {detail.metadata.titles?.map((t) => {
                    const staged = drafts.some(
                      (d) => draftKey(current, t) === draftKey(d.source, d.title),
                    );
                    return (
                      <div className="title" key={t.id}>
                        <div>
                          <strong>{t.name}</strong>
                          <small>
                            {duration(t.duration)}
                            {t.chapters != null
                              ? ` · ${t.chapters} ${t.chapters === 1 ? "chapter" : "chapters"}`
                              : ""}
                          </small>
                        </div>
                        <button
                          disabled={busy || staged}
                          onClick={() => run(() => addTitle(current, t))}
                        >
                          {staged ? "Added" : "Add to batch"}
                        </button>
                      </div>
                    );
                  })}
                </div>
                {drafts.length > 0 && (
                  <button className="primary full" onClick={review}>
                    Review batch ({drafts.length})
                  </button>
                )}
              </>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}

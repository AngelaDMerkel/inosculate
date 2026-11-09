import { useState } from "react";
import Preview from "./Preview";
import { api, Draft, draftKey, Track } from "./model";

interface Props {
  drafts: Draft[];
  change: (items: Draft[]) => void;
  finish: () => void;
}

export default function Batch({ drafts, change, finish }: Props) {
  const [audio, setAudio] = useState("*");
  const [subs, setSubs] = useState("*");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sample, setSample] = useState<{ draft: Draft; track: Track } | null>(null);
  const languages = (type: string) =>
    Array.from(
      new Set(
        drafts.flatMap((d) =>
          (d.title.tracks || []).filter((t) => t.type === type).map((t) => t.language || "und"),
        ),
      ),
    ).sort();
  function apply() {
    const skipped: string[] = [];
    change(
      drafts.map((d) => {
        const tracks = d.title.tracks || [];
        const audioTracks = tracks.filter((t) => t.type === "audio");
        const missing =
          audio !== "*" &&
          audioTracks.length > 0 &&
          !audioTracks.some((t) => (t.language || "und") === audio);
        if (missing) skipped.push(d.source.name);
        const keys = tracks
          .filter((t) => {
            if (t.type === "audio")
              return missing
                ? d.keys.includes(t.key)
                : audio === "*" || (t.language || "und") === audio;
            if (t.type === "subtitle") return subs === "*" || (t.language || "und") === subs;
            return d.keys.includes(t.key);
          })
          .map((t) => t.key);
        return { ...d, keys };
      }),
    );
    setNote(
      skipped.length
        ? `Kept previous audio choices where ${audio} was unavailable: ${skipped.join(", ")}.`
        : "Language choices applied.",
    );
  }
  function edit(index: number, patch: Partial<Draft>) {
    change(drafts.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }
  async function queue() {
    setBusy(true);
    setError("");
    try {
      await api("batch", {
        items: drafts.map((d) => ({
          source_id: d.source.id,
          mtime: d.source.mtime,
          title_id: d.title.id,
          output_kind: d.library,
          relative_path: d.filename,
          selected_tracks: d.keys,
        })),
      });
      finish();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Review batch</h1>
          <p>Choose the tracks and destination for each output.</p>
        </div>
        <button className="primary" disabled={busy || !drafts.length} onClick={queue}>
          {busy ? "Queuing…" : `Queue ${drafts.length} ${drafts.length === 1 ? "job" : "jobs"}`}
        </button>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {!drafts.length ? (
        <div className="empty">
          <h2>Your batch is empty</h2>
          <p>Choose titles or files from the library.</p>
        </div>
      ) : (
        <fieldset disabled={busy}>
          <div className="policy">
            <h2>Batch track selection</h2>
            <label>
              Keep audio
              <select value={audio} onChange={(e) => setAudio(e.target.value)}>
                <option value="*">All languages</option>
                {languages("audio").map((l) => (
                  <option key={l}>{l}</option>
                ))}
              </select>
            </label>
            <label>
              Keep subtitles
              <select value={subs} onChange={(e) => setSubs(e.target.value)}>
                <option value="*">All languages</option>
                <option value="-">No subtitles</option>
                {languages("subtitle").map((l) => (
                  <option key={l}>{l}</option>
                ))}
              </select>
            </label>
            <button onClick={apply}>Apply to batch</button>
          </div>
          {note && <p role="status">{note}</p>}
          <div className="batch-list">
            {drafts.map((d, i) => (
              <article className="batch-item" key={draftKey(d.source, d.title)}>
                <header>
                  <div>
                    <small>{d.source.kind === "file" ? "Media file" : d.title.name}</small>
                    <h2>{d.source.name}</h2>
                  </div>
                  <button
                    className="quiet"
                    onClick={() => change(drafts.filter((_, n) => n !== i))}
                  >
                    Remove
                  </button>
                </header>
                <div className="destination">
                  <label>
                    Output library
                    <select
                      value={d.library}
                      onChange={(e) => edit(i, { library: e.target.value })}
                    >
                      <option value="movie">Movies</option>
                      <option value="television">Television</option>
                    </select>
                  </label>
                  <label>
                    Output filename or relative path
                    <input
                      required
                      value={d.filename}
                      onChange={(e) => edit(i, { filename: e.target.value })}
                    />
                  </label>
                </div>
                <details open={drafts.length === 1 || undefined}>
                  <summary>
                    Tracks · {d.keys.length} of{" "}
                    {(d.title.tracks || []).filter((t) => t.type !== "other").length} selected
                  </summary>
                  {(d.title.tracks || [])
                    .filter((t) => t.type !== "other")
                    .map((t) => (
                      <div className="track" key={t.key}>
                        <input
                          type="checkbox"
                          aria-label={
                            "Keep " + t.type + " track " + t.key + " " + (t.name || t.codec)
                          }
                          checked={d.keys.includes(t.key)}
                          onChange={(e) =>
                            edit(i, {
                              keys: e.target.checked
                                ? [...d.keys, t.key]
                                : d.keys.filter((k) => k !== t.key),
                            })
                          }
                        />
                        <span className="type">{t.type}</span>
                        <strong>{t.name || t.codec}</strong>
                        <span>
                          {[t.language || "und", t.forced ? "forced" : ""]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                        {t.type !== "video" && (
                          <button
                            className="quiet"
                            onClick={() => setSample({ draft: d, track: t })}
                            aria-label={"Preview " + t.type + " track " + t.key}
                          >
                            Sample
                          </button>
                        )}
                      </div>
                    ))}
                </details>
              </article>
            ))}
          </div>
        </fieldset>
      )}
      {sample && <Preview {...sample} close={() => setSample(null)} />}
    </section>
  );
}

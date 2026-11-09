import { FormEvent, useState } from "react";
import { api, Preferences, Settings } from "./model";

export interface Cleanup {
  id: number;
  source_name: string;
  source_path: string;
  output_path: string;
  status: string;
  error?: string;
}
export function Processed({
  items,
  settings,
  reload,
}: {
  items: Cleanup[];
  settings: Settings;
  reload: () => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  async function decide(item: Cleanup, decision: string) {
    const remove = decision === "delete" || decision === "trash";
    if (
      remove &&
      !window.confirm(
        `${decision === "trash" ? "Move this original to trash" : "Permanently delete this original"}?\n\n${item.source_path}\n\nVerified output: ${item.output_path}`,
      )
    )
      return;
    setBusy(true);
    try {
      await api(`cleanup/${item.id}`, { decision, confirm: remove });
      await reload();
      setNote(`Decision saved for ${item.source_name}`);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Processed</h1>
          <p>Choose what to do with each original after verifying its output.</p>
        </div>
      </div>
      {note && <p role="status">{note}</p>}
      <fieldset disabled={busy} className="batch-list">
        {items.map((i) => (
          <article key={i.id} className="batch-item">
            <h2>{i.source_name}</h2>
            <p>
              Original <code>{i.source_path}</code>
            </p>
            <p>
              Verified output <code>{i.output_path}</code>
            </p>
            {i.error && <p className="alert">{i.error}</p>}
            <div className="actions">
              {[
                ["keep", "Keep original"],
                ["later", "Decide later"],
                ...(settings.trash_enabled ? [["trash", "Move to trash"]] : []),
                ["delete", "Delete permanently"],
              ].map(([k, text]) => (
                <button key={k} onClick={() => decide(i, k)}>
                  {text}
                </button>
              ))}
            </div>
          </article>
        ))}
      </fieldset>
      {!items.length && <p className="empty">No verified outputs await a cleanup decision.</p>}
    </section>
  );
}

export function SettingsPage({
  settings,
  prefs,
  save,
}: {
  settings: Settings;
  prefs: Preferences;
  save: (p: Preferences) => void;
}) {
  const [form, setForm] = useState(prefs);
  const [patterns, setPatterns] = useState(prefs.ignored_patterns.join("\n"));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      save(
        await api<Preferences>("preferences", { ...form, ignored_patterns: patterns.split("\n") }),
      );
      setNote("Preferences saved.");
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      <form onSubmit={submit} className="batch-item">
        <fieldset disabled={busy}>
          <h2>Track defaults</h2>
          <div className="editor-fields">
            {(["audio", "subtitle"] as const).map((type) => (
              <label key={type}>
                Preferred {type} language
                <select
                  value={form[`preferred_${type}_language`]}
                  onChange={(e) =>
                    setForm({ ...form, [`preferred_${type}_language`]: e.target.value })
                  }
                >
                  <option value="">No preference</option>
                  {settings.languages.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <label>
            <input
              type="checkbox"
              checked={form.forced_subtitles_only}
              onChange={(e) => setForm({ ...form, forced_subtitles_only: e.target.checked })}
            />{" "}
            Prefer forced subtitles only
          </label>
          <label className="field">
            Ignored folders and patterns
            <textarea rows={6} value={patterns} onChange={(e) => setPatterns(e.target.value)} />
            <small>One pattern per line. Scan again after saving.</small>
          </label>
          <button className="primary">Save preferences</button>
        </fieldset>
      </form>
      {note && <p role="status">{note}</p>}
      <section className="batch-item">
        <h2>Docker configuration</h2>
        <p>
          Import <code>{settings.ingest}</code> · Movies <code>{settings.movies}</code> · Television{" "}
          <code>{settings.television}</code>
        </p>
        <p>
          {settings.concurrency.remux} remux · {settings.concurrency.probe} inspections ·{" "}
          {settings.concurrency.preview} previews
        </p>
      </section>
    </section>
  );
}

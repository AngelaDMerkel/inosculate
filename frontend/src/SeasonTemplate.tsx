import { useEffect, useRef, useState } from "react";
import { api, Draft } from "./model";

interface Item {
  source_id: number;
  name: string;
  matched: boolean;
  relative_path: string;
  reason: string;
  probe_status: string;
}
export default function SeasonTemplate({
  draft,
  close,
  notify,
  onScheduled,
}: {
  draft: Draft;
  close: () => void;
  notify: (s: string) => void;
  onScheduled: (d: Draft, ids?: number[]) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [ids, setIds] = useState<number[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [body] = useState(() => ({
    series_name: draft.source.series_name,
    season_number: draft.source.season_number ?? 1,
    template_source_id: draft.source.id,
    title_id: draft.title.id,
    selected_tracks: draft.keys,
    track_overrides: draft.overrides || {},
    output_kind: draft.library,
    relative_directory: draft.filename.split("/").slice(0, -1).join("/"),
  }));
  useEffect(() => {
    const el = dialog.current!;
    el.showModal();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const result = await api<{ items: Item[] }>("seasons/preflight", body);
        if (!active) return;
        setItems(result.items);
        if (
          result.items.some((i) => ["inspection_queued", "inspecting"].includes(i.probe_status))
        ) {
          if (active) timer = setTimeout(check, 1500);
          return;
        }
        setIds(result.items.filter((i) => i.matched).map((i) => i.source_id));
        setBusy(false);
      } catch (e) {
        if (active) {
          setError((e as Error).message);
          setBusy(false);
        }
      }
    }
    api("seasons/analyze", body)
      .then(() => {
        if (active) check();
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setBusy(false);
        }
      });
    return () => {
      active = false;
      clearTimeout(timer);
      el.close();
    };
  }, [body]);
  async function schedule() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ scheduled: number; source_ids: number[] }>("seasons/schedule", {
        ...body,
        confirm: true,
        source_ids: ids,
      });
      onScheduled(draft, result.source_ids);
      notify(`Scheduled ${result.scheduled} matching episodes`);
      close();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="season-dialog"
      onCancel={close}
      aria-label="Season template preflight"
    >
      <div className="title">
        <h2>Season template preflight</h2>
        <button onClick={close}>Close</button>
      </div>
      <p>
        {busy
          ? "Checking season…"
          : `${items.filter((i) => i.matched).length} matching · ${items.filter((i) => !i.matched).length} exceptions`}
      </p>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {items.map((i) => (
        <label className="season-item" key={i.source_id}>
          <input
            type="checkbox"
            checked={ids.includes(i.source_id)}
            disabled={busy || !i.matched}
            onChange={(e) =>
              setIds(
                e.target.checked ? [...ids, i.source_id] : ids.filter((id) => id !== i.source_id),
              )
            }
          />
          <span>
            {i.name}
            <small>{i.matched ? i.relative_path : i.reason}</small>
          </span>
        </label>
      ))}
      <button className="primary" disabled={busy || !ids.length} onClick={schedule}>
        Schedule {ids.length} episodes
      </button>
    </dialog>
  );
}

import { useEffect, useRef, useState } from "react";
import { api, Draft, Track } from "./model";

interface Result {
  status: string;
  error?: string;
  audio_url?: string;
  image_url?: string;
  result?: { snippets?: { time: string; text: string }[] };
}

export default function Preview({
  draft,
  track,
  close,
}: {
  draft: Draft;
  track: Track;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState<Result | null>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll(id: number) {
      try {
        const result = await api<Result>(`previews/${id}`);
        if (!active) return;
        setValue(result);
        if (["queued", "running"].includes(result.status)) timer = setTimeout(() => poll(id), 1000);
      } catch (e) {
        if (active) setValue({ status: "failed", error: (e as Error).message });
      }
    }
    api<{ id: number }>("previews", {
      source_id: draft.source.id,
      title_id: draft.title.id,
      track_key: track.key,
      preview_type: track.type,
    })
      .then((r) => {
        if (active) poll(r.id);
      })
      .catch((e) => {
        if (active) setValue({ status: "failed", error: e.message });
      });
    return () => {
      active = false;
      clearTimeout(timer);
      element.close();
    };
  }, [draft.source.id, draft.title.id, track.key, track.type]);
  return (
    <dialog ref={dialog} aria-labelledby="preview-name" onCancel={close}>
      <div className="title">
        <h2 id="preview-name">{track.type === "audio" ? "Audio" : "Subtitle"} sample</h2>
        <button onClick={close}>Close</button>
      </div>
      <p>
        {draft.source.name} · {track.name || track.codec} · {track.language || "und"}
      </p>
      {!value || ["queued", "running"].includes(value.status) ? (
        <p role="status">Preparing sample…</p>
      ) : value.error ? (
        <p className="alert" role="alert">
          {value.error}
        </p>
      ) : (
        <>
          {value.audio_url && <audio controls src={value.audio_url} />}
          {value.image_url && (
            <img alt="Frame from the selected subtitle track" src={value.image_url} />
          )}
          {value.result?.snippets?.map((s, i) => (
            <p key={i}>
              <small>{s.time}</small>
              {s.text}
            </p>
          ))}
        </>
      )}
    </dialog>
  );
}

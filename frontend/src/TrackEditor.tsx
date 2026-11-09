import { useState } from "react";
import { Draft, Settings, sameLanguage, Track } from "./model";
import Preview from "./Preview";

export default function TrackEditor({
  draft,
  settings,
  change,
}: {
  draft: Draft;
  settings: Settings;
  change: (patch: Partial<Draft>) => void;
}) {
  const [sample, setSample] = useState<Track | null>(null);
  function override(
    key: string,
    patch: { language?: string; name?: string; clear_name?: boolean },
  ) {
    change({ overrides: { ...draft.overrides, [key]: { ...draft.overrides?.[key], ...patch } } });
  }
  return (
    <div className="track-editor">
      {(draft.title.tracks || [])
        .filter((t) => t.type !== "other")
        .map((t) => {
          const selected = draft.keys.includes(t.key);
          const detail = [
            t.codec,
            t.audio_channels &&
              `${t.audio_channels} ${t.audio_channels === 1 ? "channel" : "channels"}`,
            t.resolution,
            t.resolution_class,
            t.default && "Default",
            t.forced && "Forced",
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <div className="track-choice" key={t.key}>
              <label className="track-include">
                <input
                  type="checkbox"
                  checked={selected}
                  aria-label={`Keep ${t.type} track ${t.key}`}
                  onChange={(e) =>
                    change({
                      keys: e.target.checked
                        ? [...draft.keys, t.key]
                        : draft.keys.filter((k) => k !== t.key),
                    })
                  }
                />
                <span>
                  <strong>
                    {t.type} track {t.key}
                  </strong>
                  <small>{detail}</small>
                </span>
              </label>
              {t.type !== "video" && (
                <>
                  <label>
                    Language
                    <select
                      value={draft.overrides?.[t.key]?.language || ""}
                      onChange={(e) => override(t.key, { language: e.target.value })}
                    >
                      <option value="">
                        Source ·{" "}
                        {settings.languages.find((l) => sameLanguage(l.code, t.language))?.name ||
                          t.language ||
                          "Unknown"}
                      </option>
                      {settings.languages.map((l) => (
                        <option key={l.code} value={l.code}>
                          {l.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Track name
                    <input
                      value={draft.overrides?.[t.key]?.name ?? t.name ?? ""}
                      onChange={(e) =>
                        override(t.key, {
                          name: e.target.value,
                          clear_name: !e.target.value && !!t.name,
                        })
                      }
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => setSample(t)}
                    aria-label={`Preview ${t.type} track ${t.key}`}
                  >
                    {t.type === "audio" ? "Listen" : "Preview subtitles"}
                  </button>
                </>
              )}
            </div>
          );
        })}
      {sample && <Preview draft={draft} track={sample} close={() => setSample(null)} />}
    </div>
  );
}

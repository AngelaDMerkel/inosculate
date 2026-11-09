import { useCallback, useEffect, useRef, useState } from "react";
import Batch from "./Batch";
import Jobs from "./Jobs";
import Library from "./Library";
import { Cleanup, Processed, SettingsPage } from "./Management";
import { api, Draft, draftKey, Job, Source, Settings, Preferences } from "./model";

const tabs = ["Library", "Batch", "Jobs", "Processed", "Settings"] as const;
type Tab = (typeof tabs)[number];
const draftStorage = "inosculate.batch.v1";
function savedBatch(): Draft[] {
  try {
    const items = JSON.parse(sessionStorage.getItem(draftStorage) || "[]");
    return Array.isArray(items)
      ? items.filter((d) => d.source && d.title && Array.isArray(d.keys))
      : [];
  } catch {
    return [];
  }
}
export default function App() {
  const [tab, setTab] = useState<Tab>("Library");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [configError, setConfigError] = useState("");
  const [configAttempt, retryConfig] = useState(0);
  const remembered = useRef(new Map<string, Draft>());
  useEffect(() => {
    let live = true;
    Promise.all([api<Settings>("settings"), api<Preferences>("preferences")])
      .then(([config, preferences]) => {
        if (live) {
          setSettings(config);
          setPrefs(preferences);
          setConfigError("");
        }
      })
      .catch((e) => {
        if (live) setConfigError(e.message);
      });
    return () => {
      live = false;
    };
  }, [configAttempt]);
  const [cleanup, setCleanup] = useState<Cleanup[]>([]);
  const loadCleanup = useCallback(async () => {
    setCleanup(await api<Cleanup[]>("cleanup"));
  }, []);
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>(savedBatch);
  const [counts, setCounts] = useState({
    awaiting: 0,
    unfinished: 0,
    running: 0,
    queued: 0,
    cleanup: 0,
  });
  const [history, setHistory] = useState(50);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    const library = await api<Source[]>("library");
    setSources(library);
  }, []);
  const loadJobs = useCallback(async () => {
    const result = await api<{ jobs: Job[]; more: boolean }>(`activity?history=${history}`);
    setJobs(result.jobs);
    setMore(result.more);
  }, [history]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [status] = await Promise.all([
          api<typeof counts>("status"),
          tab === "Library"
            ? reload()
            : tab === "Jobs"
              ? loadJobs()
              : tab === "Processed"
                ? loadCleanup()
                : undefined,
        ]);
        if (active) setCounts(status);
        if (active) setError("");
      } catch (e) {
        if (active) setError((e as Error).message);
      }
      if (active) timer = setTimeout(refresh, 2500);
    }
    refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [tab, reload, loadJobs, loadCleanup]);
  useEffect(() => {
    try {
      sessionStorage.setItem(draftStorage, JSON.stringify(drafts));
    } catch {
      /* The in-memory draft remains usable when storage is unavailable. */
    }
  }, [drafts]);
  function add(items: Draft[]) {
    setDrafts((previous) => {
      const keys = new Set(previous.map((d) => draftKey(d.source, d.title)));
      return [...previous, ...items.filter((d) => !keys.has(draftKey(d.source, d.title)))];
    });
  }
  function stage(draft: Draft) {
    const key = draftKey(draft.source, draft.title);
    setDrafts((items) =>
      items.some((d) => draftKey(d.source, d.title) === key)
        ? items.map((d) => (draftKey(d.source, d.title) === key ? draft : d))
        : [...items, draft],
    );
  }
  function scheduled(draft: Draft, seasonSources?: number[]) {
    const key = draftKey(draft.source, draft.title);
    setDrafts((items) =>
      items.filter((d) =>
        seasonSources ? !seasonSources.includes(d.source.id) : draftKey(d.source, d.title) !== key,
      ),
    );
    remembered.current.delete(key);
    reload().catch((e) => setError(e.message));
  }
  const badges = {
    Library: counts.awaiting,
    Batch: drafts.length,
    Jobs: counts.unfinished,
    Processed: counts.cleanup,
    Settings: 0,
  };
  return (
    <>
      <header className="mast">
        <div className="mast-inner">
          <div className="brand">
            <i className="disc" aria-hidden="true" />
            Inosculate
          </div>
          <nav aria-label="Workspace">
            {tabs.map((t) => (
              <button
                key={t}
                aria-label={t}
                aria-current={t === tab ? "page" : undefined}
                onClick={() => setTab(t)}
              >
                {t}
                {badges[t] > 0 && <b>{badges[t]}</b>}
              </button>
            ))}
          </nav>
          {counts.running > 0 && (
            <span className="worker-active">Worker active · {counts.running} running</span>
          )}
        </div>
      </header>
      <main>
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        {configError && (
          <p role="alert" className="alert">
            {configError} <button onClick={() => retryConfig((n) => n + 1)}>Retry</button>
          </p>
        )}
        {!settings && !configError && <p role="status">Loading settings…</p>}
        {tab === "Library" && settings && prefs && (
          <Library
            sources={sources}
            settings={settings}
            prefs={prefs}
            remembered={remembered.current}
            stage={stage}
            scheduled={scheduled}
            drafts={drafts}
            add={add}
            reload={reload}
            review={() => setTab("Batch")}
          />
        )}
        {tab === "Batch" && settings && (
          <Batch
            drafts={drafts}
            settings={settings}
            change={setDrafts}
            finish={() => {
              setDrafts([]);
              setTab("Jobs");
            }}
          />
        )}
        {tab === "Jobs" && (
          <Jobs
            jobs={jobs}
            reload={loadJobs}
            more={more}
            loadMore={() => setHistory((n) => n + 50)}
          />
        )}
        {tab === "Processed" && settings && (
          <Processed items={cleanup} settings={settings} reload={loadCleanup} />
        )}
        {tab === "Settings" && settings && prefs && (
          <SettingsPage settings={settings} prefs={prefs} save={setPrefs} />
        )}
      </main>
    </>
  );
}

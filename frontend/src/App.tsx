import { useCallback, useEffect, useState } from "react";
import Batch from "./Batch";
import Jobs from "./Jobs";
import Library from "./Library";
import { api, Draft, draftKey, Job, Source } from "./model";

const tabs = ["Library", "Batch", "Jobs"] as const;
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
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>(savedBatch);
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
        if (tab === "Library") await reload();
        if (tab === "Jobs") await loadJobs();
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
  }, [tab, reload, loadJobs]);
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
                aria-current={t === tab ? "page" : undefined}
                onClick={() => setTab(t)}
              >
                {t}
                {t === "Batch" && drafts.length > 0 && <b>{drafts.length}</b>}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main>
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        {tab === "Library" && (
          <Library
            sources={sources}
            drafts={drafts}
            add={add}
            reload={reload}
            review={() => setTab("Batch")}
          />
        )}
        {tab === "Batch" && (
          <Batch
            drafts={drafts}
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
      </main>
    </>
  );
}

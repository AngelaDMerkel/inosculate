import { useState } from "react";
import { api, Job } from "./model";

export default function Jobs({
  jobs,
  reload,
  more,
  loadMore,
}: {
  jobs: Job[];
  reload: () => Promise<void>;
  more: boolean;
  loadMore: () => void;
}) {
  const [log, setLog] = useState<{ id: number; text: string } | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [pending, setPending] = useState<number | null>(null);
  async function action(job: Job, name: string) {
    setPending(job.id);
    setError("");
    try {
      if (name === "log") {
        const result = await api<{ log: string }>(`jobs/${job.id}/log`);
        setLog({ id: job.id, text: result.log || "No output yet." });
      } else {
        await api(`jobs/${job.id}/${name}`, {});
        await reload();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(null);
    }
  }
  const active = ["queued", "running", "awaiting_overwrite"];
  const visible = jobs.filter(
    (j) =>
      filter === "all" ||
      (filter === "active"
        ? active.includes(j.status)
        : filter === "issues"
          ? ["failed", "cancelled", "retryable", "awaiting_overwrite", "needs_selection"].includes(
              j.status,
            )
          : j.status === "complete"),
  );
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Jobs</h1>
          <p>Follow each output from the queue through verification.</p>
        </div>
        <select aria-label="Filter jobs" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All jobs</option>
          <option value="active">Active</option>
          <option value="issues">Needs attention</option>
          <option value="done">Completed</option>
        </select>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {!visible.length && (
        <div className="empty">
          <h2>No jobs here</h2>
          <p>Queued titles appear here when you submit a batch.</p>
        </div>
      )}
      <div className="batch-list">
        {visible.map((j) => (
          <article className="batch-item" key={j.id}>
            <header>
              <div>
                <small>
                  Job {j.id} · {j.source_name}
                </small>
                <h2>{j.relative_path}</h2>
                <small>
                  {j.output_path} · {new Date(j.started_at || j.created_at).toLocaleString()}
                </small>
              </div>
              <span className="state">{j.status.replaceAll("_", " ")}</span>
            </header>
            {j.status === "running" && (
              <>
                <progress max={100} value={j.progress} />
                <small>
                  {j.phase} · {Math.floor(j.progress)}%
                </small>
              </>
            )}
            {j.error && <p className="alert">{j.error}</p>}
            <div className="actions">
              {active.includes(j.status) && (
                <button
                  disabled={pending === j.id || j.cancel_requested}
                  onClick={() => action(j, "cancel")}
                >
                  {j.cancel_requested ? "Cancelling…" : "Cancel"}
                </button>
              )}
              {["failed", "retryable", "cancelled"].includes(j.status) && (
                <button disabled={pending === j.id} onClick={() => action(j, "retry")}>
                  Retry
                </button>
              )}
              {j.status === "awaiting_overwrite" && (
                <button
                  onClick={() => {
                    if (
                      window.confirm("Overwrite the existing destination file?\n\n" + j.output_path)
                    )
                      action(j, "overwrite");
                  }}
                >
                  Replace existing output
                </button>
              )}
              <button
                className="quiet"
                onClick={() => (log?.id === j.id ? setLog(null) : action(j, "log"))}
              >
                {log?.id === j.id ? "Hide log" : "View log"}
              </button>
            </div>
            {log?.id === j.id && <pre>{log.text}</pre>}
          </article>
        ))}
      </div>
      {more && filter !== "active" && <button onClick={loadMore}>Load older jobs</button>}
    </section>
  );
}

const state = {
  settings: null,
  preferences: null,
  sources: [],
  jobs: [],
  cleanup: [],
  directories: {movie: null, television: null},
  expanded: new Set(),
  selected: new Set(),
  drafts: new Map(),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? {"Content-Type": "application/json"} : {},
    ...options,
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[char]);
}

let toastTimer;
function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("visible"), 5000);
}

function statusBadge(status) {
  const label = String(status || "discovered").replaceAll("_", " ");
  return `<span class="badge ${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

function languageOptions(selected = "", includeBlank = false) {
  const options = [];
  if (includeBlank) options.push(`<option value="">Keep reported language</option>`);
  for (const language of state.settings?.languages || []) {
    options.push(`<option value="${language.code}" ${language.code === selected ? "selected" : ""}>${escapeHtml(language.name)} (${language.code})</option>`);
  }
  return options.join("");
}

function sourceTitleSummary(source) {
  const title = preferredTitle(source);
  if (!title) return "Not inspected";
  const tracks = title.tracks || [];
  return [
    title.duration_display,
    title.resolution_display,
    `${tracks.filter(track => track.type === "audio").length} audio`,
    `${tracks.filter(track => track.type === "subtitle").length} subtitles`,
  ].filter(Boolean).join(" · ");
}

function preferredTitle(source, titleId = null) {
  const titles = source.metadata?.titles || [];
  if (titleId !== null) return titles.find(title => String(title.id) === String(titleId));
  return titles.find(title => title.suggested) || titles[0] || null;
}

function safeName(value) {
  return String(value || "").trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, "").replace(/\s+/g, " ") || "Untitled";
}

function defaultFilename(source) {
  if (source.series_name && source.episode_number !== null) {
    const season = String(source.season_number || 1).padStart(2, "0");
    const episode = String(source.episode_number).padStart(2, "0");
    const title = source.episode_title ? ` - ${safeName(source.episode_title)}` : "";
    return `${safeName(source.series_name)} - S${season}E${episode}${title}.mkv`;
  }
  return `${safeName(source.name)}.mkv`;
}

function defaultDirectory(source) {
  if (!source.series_name) return "";
  const season = String(source.season_number || 1).padStart(2, "0");
  return `${safeName(source.series_name)}/Season ${season}`;
}

function createDraft(source) {
  const title = preferredTitle(source);
  const outputKind = source.series_name ? "television" : "movie";
  const draft = {
    titleId: title ? String(title.id) : "0",
    outputKind,
    directory: "",
    newDirectory: defaultDirectory(source),
    filename: defaultFilename(source),
    tracks: {},
    subtitleResults: {},
  };
  if (title) applyTrackDefaults(draft, title);
  state.drafts.set(source.id, draft);
  return draft;
}

function draftFor(source) {
  return state.drafts.get(source.id) || createDraft(source);
}

function applyTrackDefaults(draft, title) {
  const preferredAudio = state.preferences?.preferred_audio_language || "";
  const preferredSubtitle = state.preferences?.preferred_subtitle_language || "";
  const audioTracks = title.tracks.filter(track => track.type === "audio");
  const subtitleTracks = title.tracks.filter(track => track.type === "subtitle");
  const audioHasPreferred = audioTracks.some(track => track.language === preferredAudio);
  const subtitleHasPreferred = subtitleTracks.some(track => track.language === preferredSubtitle);
  draft.tracks = {};
  for (const track of title.tracks) {
    let selected = track.type === "video";
    if (track.type === "audio") selected = preferredAudio && audioHasPreferred ? track.language === preferredAudio : true;
    if (track.type === "subtitle") {
      if (state.preferences?.forced_subtitles_only) selected = Boolean(track.forced);
      else selected = preferredSubtitle && subtitleHasPreferred ? track.language === preferredSubtitle : true;
    }
    draft.tracks[String(track.key)] = {
      selected,
      language: "",
      name: "",
    };
  }
}

function groupSources(sources) {
  const groups = new Map();
  const ungrouped = [];
  for (const source of sources) {
    if (!source.series_name) {
      ungrouped.push(source);
      continue;
    }
    const key = source.group_key || source.series_name;
    if (!groups.has(key)) groups.set(key, {name: source.series_name, items: []});
    groups.get(key).items.push(source);
  }
  return [...groups.values()]
    .sort((a, b) => a.name.localeCompare(b.name))
    .concat(ungrouped.length ? [{name: "Ungrouped media", items: ungrouped, ungrouped: true}] : []);
}

function renderSources() {
  const needle = $("#source-filter").value.trim().toLowerCase();
  const filtered = state.sources.filter(source =>
    `${source.name} ${source.series_name || ""} ${source.path}`.toLowerCase().includes(needle)
  );
  $("#source-count").textContent = state.sources.length || "";
  const groups = groupSources(filtered);
  $("#source-list").innerHTML = groups.length ? groups.map(renderGroup).join("") :
    `<div class="empty">${state.sources.length ? "No media matches that search." : "No media yet. Scan ingest to build the inventory."}</div>`;
}

function renderGroup(group) {
  const seasons = [...new Set(group.items.map(item => item.season_number).filter(value => value !== null))];
  return `<section class="media-group">
    <div class="group-heading">
      <div>
        <h3>${escapeHtml(group.name)}</h3>
        <span>${group.items.length} item${group.items.length === 1 ? "" : "s"}${seasons.length ? ` · ${seasons.length} season${seasons.length === 1 ? "" : "s"}` : ""}</span>
      </div>
      <div class="group-actions">
        <button data-select-group="${escapeHtml(group.name)}">Select group</button>
        <button data-inspect-selected>Inspect selected</button>
        <button class="primary" data-queue-selected>Queue configured</button>
      </div>
    </div>
    <div class="media-rows">${group.items.map(renderSource).join("")}</div>
  </section>`;
}

function renderSource(source) {
  const expanded = state.expanded.has(source.id);
  const queueStatus = source.active_job_count ? "Already queued" :
    source.completed_job_count ? "Processed" : "";
  return `<article class="media-row ${expanded ? "expanded" : ""}" data-source-row="${source.id}">
    <div class="media-summary">
      <input class="source-select" type="checkbox" data-select-source="${source.id}" ${state.selected.has(source.id) ? "checked" : ""} aria-label="Select ${escapeHtml(source.name)}">
      <div class="media-name">
        <strong>${escapeHtml(source.episode_number !== null ? `E${String(source.episode_number).padStart(2, "0")} · ${source.episode_title || source.name}` : source.name)}</strong>
        <span class="path">${escapeHtml(source.path)}</span>
      </div>
      <div class="media-facts">
        ${statusBadge(source.probe_status)}
        <span>${escapeHtml(source.kind.toUpperCase())}</span>
        <span>${escapeHtml(source.size_display)}</span>
        <span>${escapeHtml(sourceTitleSummary(source))}</span>
        ${queueStatus ? `<span class="queue-state">${queueStatus}</span>` : ""}
      </div>
      <button class="dropdown-button" data-toggle-source="${source.id}">${expanded ? "Close" : "Tracks & destination"} <span>⌄</span></button>
    </div>
    ${expanded ? renderInlinePanel(source) : ""}
  </article>`;
}

function renderInlinePanel(source) {
  if (["discovered", "inspection_queued", "inspecting"].includes(source.probe_status)) {
    return `<div class="inline-panel inspecting"><span class="spinner"></span>${source.probe_status === "discovered" ? "Waiting to inspect…" : "Inspecting media tracks on request…"}</div>`;
  }
  if (source.probe_status === "error") {
    return `<div class="inline-panel"><div class="notice error">${escapeHtml(source.probe_error)}</div><button data-inspect-source="${source.id}">Retry inspection</button></div>`;
  }
  const draft = draftFor(source);
  const title = preferredTitle(source, draft.titleId) || preferredTitle(source);
  if (!title) return `<div class="inline-panel">No title metadata was reported.</div>`;
  const titles = source.metadata.titles || [];
  return `<div class="inline-panel" data-inline-source="${source.id}">
    ${titles.length > 1 ? `<label class="compact-field">Disc title
      <select data-draft-title="${source.id}">${titles.map(value => `<option value="${escapeHtml(value.id)}" ${String(value.id) === draft.titleId ? "selected" : ""}>${escapeHtml(value.name)} · ${value.duration_display} · ${value.resolution_display}${value.suggested ? " · suggested" : ""}</option>`).join("")}</select>
    </label>` : ""}
    <div class="resolution-strip"><strong>${escapeHtml(title.resolution || "Unknown")}</strong><span>${escapeHtml(title.resolution_class || "Unknown")}</span><span>${escapeHtml(title.duration_display)}</span></div>
    <div class="track-grid">${title.tracks.map(track => renderTrack(source, draft, track)).join("")}</div>
    <div class="destination-grid">
      <label>Library
        <select data-output-kind="${source.id}">
          <option value="movie" ${draft.outputKind === "movie" ? "selected" : ""}>Movies</option>
          <option value="television" ${draft.outputKind === "television" ? "selected" : ""}>Television</option>
        </select>
      </label>
      <label>Existing directory
        <select data-directory="${source.id}">${directoryOptions(draft.outputKind, draft.directory)}</select>
      </label>
      <label>New/subdirectory
        <input data-new-directory="${source.id}" value="${escapeHtml(draft.newDirectory)}" placeholder="Optional relative folder">
      </label>
      <label>Output filename
        <input data-filename="${source.id}" value="${escapeHtml(draft.filename)}">
      </label>
    </div>
    <div class="queue-review">
      <span>Destination</span>
      <strong>${escapeHtml(destinationFor(source, draft))}</strong>
      <button class="primary" data-add-queue="${source.id}" ${source.active_job_count ? "disabled" : ""}>${source.active_job_count ? "Already queued" : "Add to Queue"}</button>
    </div>
  </div>`;
}

function renderTrack(source, draft, track) {
  const selection = draft.tracks[String(track.key)] || {selected: true, language: "", name: ""};
  const unknown = !track.language || ["und", "unknown"].includes(track.language);
  const preview = draft.subtitleResults[String(track.key)];
  return `<div class="track-row">
    <input type="checkbox" data-track-selected="${source.id}:${escapeHtml(track.key)}" ${selection.selected ? "checked" : ""}>
    <span class="track-type">${escapeHtml(track.type)}</span>
    <span class="track-codec">${escapeHtml(track.codec)}</span>
    <span class="track-detail">${escapeHtml([
      track.type === "video" ? `${track.resolution || ""} · ${track.resolution_class || ""}` : "",
      track.audio_channels ? `${track.audio_channels} ch` : "",
      track.name,
      track.default ? "default" : "",
      track.forced ? "forced" : "",
    ].filter(Boolean).join(" · ") || "—")}</span>
    ${["audio", "subtitle"].includes(track.type) ? `<select class="${unknown ? "needs-language" : ""}" data-track-language="${source.id}:${escapeHtml(track.key)}" title="Set output language">
      ${languageOptions(selection.language, true)}
    </select>` : `<span></span>`}
    <span class="reported-language">${escapeHtml(track.language_name || track.language || "und")}</span>
    ${track.type === "audio" && source.kind !== "iso" ? `<button data-audio-preview="${source.id}:${escapeHtml(track.key)}">▶ Audio</button>` : ""}
    ${track.type === "subtitle" && source.kind !== "iso" ? `<button data-subtitle-preview="${source.id}:${escapeHtml(track.key)}">Subtitle sample</button>` : ""}
    ${preview ? `<div class="subtitle-result">${renderSubtitleResult(preview)}</div>` : ""}
  </div>`;
}

function renderSubtitleResult(result) {
  if (result.kind === "text") {
    return result.snippets.map(cue => `<p><small>${escapeHtml(cue.time)}</small>${escapeHtml(cue.text)}</p>`).join("");
  }
  if (result.kind === "image") {
    return `<p>${escapeHtml(result.message)}</p><img src="${escapeHtml(result.imageUrl)}" alt="Subtitle preview frame">`;
  }
  return `<p>${escapeHtml(result.message || "No subtitle sample available")}</p>`;
}

function directoryOptions(kind, selected) {
  const listing = state.directories[kind];
  if (!listing) return `<option value="">Loading directories…</option>`;
  return listing.directories.map(value =>
    `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value || "/ (library root)")}</option>`
  ).join("");
}

function destinationFor(source, draft) {
  const root = draft.outputKind === "movie" ? state.settings.movies : state.settings.television;
  const directory = [draft.directory, draft.newDirectory].filter(Boolean).join("/").replaceAll("//", "/");
  return `${root}/${directory ? `${directory}/` : ""}${draft.filename}`;
}

async function ensureDirectories(kind) {
  if (state.directories[kind]) return;
  state.directories[kind] = await api(`/api/directories?kind=${kind}`);
}

async function inspectSource(sourceId) {
  const source = state.sources.find(value => value.id === Number(sourceId));
  if (!source) return;
  source.probe_status = "inspection_queued";
  renderSources();
  await api(`/api/sources/${source.id}/inspect`, {method: "POST"});
  for (let attempt = 0; attempt < 900; attempt++) {
    const updated = await api(`/api/sources/${source.id}`);
    const index = state.sources.findIndex(value => value.id === source.id);
    state.sources[index] = updated;
    if (["ready", "error"].includes(updated.probe_status)) {
      if (updated.probe_status === "ready") {
        state.drafts.delete(source.id);
        await ensureDirectories(updated.series_name ? "television" : "movie");
      }
      renderSources();
      return;
    }
    if (attempt % 3 === 0) renderSources();
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  toast("Inspection is taking longer than expected; it will continue in the worker.");
}

async function createPreview(sourceId, trackKey, previewType, button) {
  const source = state.sources.find(value => value.id === Number(sourceId));
  const draft = draftFor(source);
  button.disabled = true;
  button.textContent = "Preparing…";
  try {
    const preview = await api("/api/previews", {
      method: "POST",
      body: JSON.stringify({
        source_id: source.id,
        title_id: draft.titleId,
        track_key: trackKey,
        preview_type: previewType,
      }),
    });
    for (let attempt = 0; attempt < 240; attempt++) {
      const status = await api(`/api/previews/${preview.id}`);
      if (status.status === "complete") {
        if (previewType === "audio") {
          const audio = new Audio(status.audio_url);
          await audio.play();
          button.textContent = "❚❚ Playing";
          audio.addEventListener("ended", () => { button.textContent = "▶ Audio"; button.disabled = false; });
        } else {
          const result = {...status.result};
          if (status.image_url) result.imageUrl = status.image_url;
          draft.subtitleResults[String(trackKey)] = result;
          renderSources();
        }
        return;
      }
      if (status.status === "failed") throw new Error(status.error || "Preview failed");
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error("Preview timed out");
  } catch (error) {
    toast(error.message);
  } finally {
    if (previewType === "subtitle") {
      button.disabled = false;
      button.textContent = "Subtitle sample";
    }
  }
}

function jobPayload(source) {
  const draft = draftFor(source);
  const title = preferredTitle(source, draft.titleId);
  const selectedTracks = Object.entries(draft.tracks).filter(([, value]) => value.selected).map(([key]) => key);
  const trackOverrides = {};
  for (const [key, value] of Object.entries(draft.tracks)) {
    if (value.language || value.name) trackOverrides[key] = {language: value.language, name: value.name};
  }
  const directory = [draft.directory, draft.newDirectory].filter(Boolean).join("/");
  return {
    source_id: source.id,
    title_id: title.id,
    output_kind: draft.outputKind,
    relative_path: `${directory ? `${directory}/` : ""}${draft.filename}`,
    selected_tracks: selectedTracks,
    track_overrides: trackOverrides,
  };
}

async function queueSource(source, overwrite = false) {
  const payload = {...jobPayload(source), overwrite};
  try {
    await api("/api/jobs", {method: "POST", body: JSON.stringify(payload)});
  } catch (error) {
    if (error.status === 409 && error.payload?.code === "destination_exists") {
      if (confirm(`Overwrite existing destination?\n\n${error.payload.output_path}`)) return queueSource(source, true);
    }
    throw error;
  }
}

async function queueSelected() {
  const selected = state.sources.filter(source => state.selected.has(source.id));
  const ready = selected.filter(source => source.probe_status === "ready" && !source.active_job_count);
  if (!ready.length) return toast("Select inspected, unqueued media first.");
  const summary = ready.map(source => `• ${source.name}\n  ${destinationFor(source, draftFor(source))}`).join("\n");
  if (!confirm(`Add ${ready.length} configured item(s) to the queue?\n\n${summary}`)) return;
  for (const source of ready) await queueSource(source);
  toast(`${ready.length} item(s) added to the queue`);
  state.selected.clear();
  await refreshAll();
  activateView("queue");
}

function renderJobs() {
  $("#job-count").textContent = state.jobs.filter(job => ["queued", "running", "awaiting_overwrite"].includes(job.status)).length || "";
  $("#job-list").innerHTML = state.jobs.length ? state.jobs.map(job => `
    <article class="card">
      <div><div class="card-title">${escapeHtml(job.source_name)}</div>
        <div class="card-meta">${statusBadge(job.status)}<span>${escapeHtml(job.phase || "Waiting")}</span><span>Attempt ${job.attempts || 0}</span></div>
        <p class="path">${escapeHtml(job.output_path)}</p>
        ${["running", "complete"].includes(job.status) ? `<div class="progress"><span style="width:${Number(job.progress || 0)}%"></span></div>` : ""}
        ${job.error ? `<div class="notice error">${escapeHtml(job.error)}</div>` : ""}
        ${job.log ? `<details><summary>Job log</summary><pre>${escapeHtml(job.log)}</pre></details>` : ""}
      </div>
      <div class="card-actions">
        ${["queued", "running"].includes(job.status) ? `<button class="danger" data-job-action="cancel" data-job="${job.id}">Cancel</button>` : ""}
        ${["failed", "cancelled", "retryable"].includes(job.status) ? `<button data-job-action="retry" data-job="${job.id}">Retry</button>` : ""}
        ${job.status === "awaiting_overwrite" ? `<button class="danger" data-job-action="overwrite" data-job="${job.id}">Overwrite</button>` : ""}
      </div>
    </article>`).join("") : `<div class="empty">The remux queue is empty.</div>`;
}

function renderCleanup() {
  $("#cleanup-count").textContent = state.cleanup.length || "";
  $("#cleanup-list").innerHTML = state.cleanup.length ? state.cleanup.map(item => `
    <article class="card"><div><div class="card-title">${escapeHtml(item.source_name)}</div>
      <div class="card-meta">${statusBadge(item.status)}<span>Output verified</span></div>
      <p class="path">Source: ${escapeHtml(item.source_path)}</p><p class="path">Output: ${escapeHtml(item.output_path)}</p>
      ${item.error ? `<div class="notice error">${escapeHtml(item.error)}</div>` : ""}</div>
      <div class="card-actions"><button data-cleanup="keep" data-cleanup-id="${item.id}">Keep</button><button data-cleanup="later" data-cleanup-id="${item.id}">Later</button><button class="danger" data-cleanup="delete" data-cleanup-id="${item.id}">${state.settings?.trash_enabled ? "Move to trash" : "Delete"}</button></div>
    </article>`).join("") : `<div class="empty">No cleanup decisions are waiting.</div>`;
}

async function refreshAll(includeSources = true) {
  try {
    const requests = [api("/api/settings"), api("/api/jobs"), api("/api/cleanup")];
    if (includeSources) requests.push(api("/api/sources"));
    const [settings, jobs, cleanup, sources] = await Promise.all(requests);
    state.settings = settings;
    state.jobs = jobs;
    state.cleanup = cleanup;
    if (sources) state.sources = sources;
    $("#ingest-path").textContent = settings.ingest;
    $("#worker-summary").textContent = `${settings.concurrency.remux} remux · on-demand inspection`;
    if (sources) renderSources();
    renderJobs();
    renderCleanup();
  } catch (error) { toast(error.message); }
}

function activateView(name) {
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.view === name));
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `${name}-view`));
}

function parsePair(value) {
  const separator = value.indexOf(":");
  return [Number(value.slice(0, separator)), value.slice(separator + 1)];
}

document.addEventListener("click", async event => {
  const tab = event.target.closest(".tab");
  if (tab) activateView(tab.dataset.view);

  const toggle = event.target.closest("[data-toggle-source]");
  if (toggle) {
    const id = Number(toggle.dataset.toggleSource);
    if (state.expanded.has(id)) state.expanded.delete(id);
    else {
      state.expanded.add(id);
      const source = state.sources.find(value => value.id === id);
      if (source.probe_status === "ready") {
        await ensureDirectories(source.series_name ? "television" : "movie");
        draftFor(source);
      } else if (!["inspection_queued", "inspecting"].includes(source.probe_status)) {
        inspectSource(id);
      }
    }
    renderSources();
  }

  const inspect = event.target.closest("[data-inspect-source]");
  if (inspect) inspectSource(inspect.dataset.inspectSource);

  const selectGroup = event.target.closest("[data-select-group]");
  if (selectGroup) {
    const groupName = selectGroup.dataset.selectGroup;
    state.sources.filter(source => (source.series_name || "Ungrouped media") === groupName).forEach(source => state.selected.add(source.id));
    renderSources();
  }
  if (event.target.closest("[data-inspect-selected]")) {
    const items = state.sources.filter(source => state.selected.has(source.id) && !["ready", "inspection_queued", "inspecting"].includes(source.probe_status));
    for (const source of items) inspectSource(source.id);
  }
  if (event.target.closest("[data-queue-selected]")) queueSelected();

  const audio = event.target.closest("[data-audio-preview]");
  if (audio) {
    const [sourceId, trackKey] = parsePair(audio.dataset.audioPreview);
    createPreview(sourceId, trackKey, "audio", audio);
  }
  const subtitle = event.target.closest("[data-subtitle-preview]");
  if (subtitle) {
    const [sourceId, trackKey] = parsePair(subtitle.dataset.subtitlePreview);
    createPreview(sourceId, trackKey, "subtitle", subtitle);
  }

  const add = event.target.closest("[data-add-queue]");
  if (add) {
    const source = state.sources.find(value => value.id === Number(add.dataset.addQueue));
    try {
      await queueSource(source);
      toast("Added to queue");
      state.expanded.delete(source.id);
      await refreshAll();
    } catch (error) { toast(error.message); }
  }

  const jobAction = event.target.closest("[data-job-action]");
  if (jobAction) {
    if (jobAction.dataset.jobAction === "overwrite" && !confirm("Overwrite the existing destination file?")) return;
    await api(`/api/jobs/${jobAction.dataset.job}/${jobAction.dataset.jobAction}`, {method: "POST"});
    refreshAll();
  }

  const cleanup = event.target.closest("[data-cleanup]");
  if (cleanup) {
    const decision = cleanup.dataset.cleanup;
    const confirmed = decision === "delete" ? confirm(`Remove this original source?\n\nThe verified MKV will be kept.`) : false;
    if (decision === "delete" && !confirmed) return;
    await api(`/api/cleanup/${cleanup.dataset.cleanupId}`, {method: "POST", body: JSON.stringify({decision, confirm: confirmed})});
    toast("Cleanup decision saved");
    refreshAll();
  }
});

document.addEventListener("change", async event => {
  const sourceSelect = event.target.closest("[data-select-source]");
  if (sourceSelect) {
    const id = Number(sourceSelect.dataset.selectSource);
    sourceSelect.checked ? state.selected.add(id) : state.selected.delete(id);
  }
  const title = event.target.closest("[data-draft-title]");
  if (title) {
    const source = state.sources.find(value => value.id === Number(title.dataset.draftTitle));
    const draft = draftFor(source);
    draft.titleId = title.value;
    applyTrackDefaults(draft, preferredTitle(source, draft.titleId));
    renderSources();
  }
  const outputKind = event.target.closest("[data-output-kind]");
  if (outputKind) {
    const source = state.sources.find(value => value.id === Number(outputKind.dataset.outputKind));
    const draft = draftFor(source);
    draft.outputKind = outputKind.value;
    draft.directory = "";
    await ensureDirectories(draft.outputKind);
    renderSources();
  }
  const directory = event.target.closest("[data-directory]");
  if (directory) {
    const source = state.sources.find(value => value.id === Number(directory.dataset.directory));
    draftFor(source).directory = directory.value;
    renderSources();
  }
  const trackSelected = event.target.closest("[data-track-selected]");
  if (trackSelected) {
    const [sourceId, trackKey] = parsePair(trackSelected.dataset.trackSelected);
    const source = state.sources.find(value => value.id === sourceId);
    draftFor(source).tracks[trackKey].selected = trackSelected.checked;
  }
  const trackLanguage = event.target.closest("[data-track-language]");
  if (trackLanguage) {
    const [sourceId, trackKey] = parsePair(trackLanguage.dataset.trackLanguage);
    const source = state.sources.find(value => value.id === sourceId);
    draftFor(source).tracks[trackKey].language = trackLanguage.value;
  }
});

document.addEventListener("input", event => {
  for (const [attribute, field] of [["data-new-directory", "newDirectory"], ["data-filename", "filename"]]) {
    const target = event.target.closest(`[${attribute}]`);
    if (target) {
      const source = state.sources.find(value => value.id === Number(target.getAttribute(attribute)));
      draftFor(source)[field] = target.value;
      const review = target.closest(".inline-panel")?.querySelector(".queue-review strong");
      if (review) review.textContent = destinationFor(source, draftFor(source));
    }
  }
});

$("#scan-button").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true; button.textContent = "Scanning…";
  try {
    const result = await api("/api/scan", {method: "POST"});
    toast(result.warnings?.[0] || `Inventoried ${result.found} sources · ${result.new} new`);
    await refreshAll();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "Scan ingest"; }
});

$("#source-filter").addEventListener("input", renderSources);
$("#preferences-toggle").addEventListener("click", () => $("#preferences-panel").classList.toggle("hidden"));
$("#preferences-panel").addEventListener("submit", async event => {
  event.preventDefault();
  state.preferences = await api("/api/preferences", {
    method: "POST",
    body: JSON.stringify({
      preferred_audio_language: $("#preferred-audio").value,
      preferred_subtitle_language: $("#preferred-subtitle").value,
      forced_subtitles_only: $("#forced-only").checked,
      ignored_patterns: $("#ignored-patterns").value.split("\n"),
    }),
  });
  state.drafts.clear();
  toast("Preferences saved; scan again to apply ignored patterns.");
  $("#preferences-panel").classList.add("hidden");
  renderSources();
});

async function boot() {
  const [settings, prefs] = await Promise.all([api("/api/settings"), api("/api/preferences")]);
  state.settings = settings;
  state.preferences = prefs;
  const blankLanguage = `<option value="">No preference</option>${languageOptions()}`;
  $("#preferred-audio").innerHTML = blankLanguage;
  $("#preferred-subtitle").innerHTML = blankLanguage;
  $("#preferred-audio").value = prefs.preferred_audio_language || "";
  $("#preferred-subtitle").value = prefs.preferred_subtitle_language || "";
  $("#forced-only").checked = Boolean(prefs.forced_subtitles_only);
  $("#ignored-patterns").value = (prefs.ignored_patterns || []).join("\n");
  await refreshAll();
}

boot().catch(error => toast(error.message));
setInterval(() => refreshAll(state.expanded.size === 0), 4000);

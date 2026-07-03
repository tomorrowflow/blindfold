"""Single-file management SPA for the review inbox (ADR-0011, issue #14).

A small Vue 3 page rendered straight out of FastAPI. It consumes the
:mod:`blindfold.app` management endpoints
(``/v1/management/review-inbox`` + ``…/{id}/confirm`` + ``…/{id}/reject``)
and reactively removes triaged items from the list as the user confirms or
rejects them — protection happens immediately at request time (the engine
already minted the provisional surrogate); this view is only the human side
of the learning loop.

Embedded as a Python string (rather than mounted via :class:`StaticFiles`) so
the proxy stays a one-process install and the page is testable with the
FastAPI test client without filesystem setup.
"""

from __future__ import annotations

REVIEW_INBOX_LIST_ENDPOINT = "/v1/management/review-inbox"
REVIEW_INBOX_CONFIRM_ENDPOINT = "/v1/management/review-inbox/{id}/confirm"
REVIEW_INBOX_REJECT_ENDPOINT = "/v1/management/review-inbox/{id}/reject"

ORG_GRAPH_ENDPOINT = "/v1/management/workspaces"
ENTITY_LIST_ENDPOINT = "/v1/management/workspaces"
REIDENTIFY_ENDPOINT = "/v1/management/surrogate"


def review_inbox_html() -> str:
    """Return the SPA bundle as a self-contained HTML page."""
    return _HTML


def entity_list_html() -> str:
    """Return the entity-list SPA bundle as a self-contained HTML page (issue #32).

    Renders a compact table of all entities for one selected workspace. All rows are
    in surrogate-space — no real names are included. Real-name search is gated by
    the ``re-identifier`` role and emits an audit event on every attempt (ADR-0018).
    Per-row Reveal delegates to the re-identify endpoint (ADR-0015).
    """
    return _ENTITY_LIST_HTML


def org_graph_html() -> str:
    """Return the org-graph SPA bundle as a self-contained HTML page (issue #29).

    Renders all persons and terms for one selected workspace using Cytoscape.js
    loaded from the CDN. Nodes are labelled with their surrogates — the graph
    renders in surrogate-space by default, so viewing emits no audit events.
    Per-node reveal calls the re-identify endpoint, which requires the
    ``re-identifier`` role and emits an audit event (ADR-0015).
    """
    return _ORG_GRAPH_HTML


_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Blindfold — Review Inbox</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #222; }
  h1 { font-size: 1.4rem; }
  .empty { color: #666; font-style: italic; }
  .error { color: #b00020; }
  .item { border: 1px solid #ddd; border-radius: 4px; padding: 0.75rem 1rem; margin-bottom: 0.5rem; }
  .item header { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
  .real { font-weight: 600; }
  .surrogate { color: #555; font-family: ui-monospace, monospace; }
  .context { color: #333; margin: 0.4rem 0 0.6rem; white-space: pre-wrap; }
  .actions button { margin-right: 0.5rem; cursor: pointer; }
  button.confirm { background: #1f7a3f; color: white; border: none; padding: 0.35rem 0.75rem; border-radius: 3px; }
  button.reject  { background: #b00020; color: white; border: none; padding: 0.35rem 0.75rem; border-radius: 3px; }
  button[disabled] { opacity: 0.6; cursor: progress; }
</style>
</head>
<body>
  <div id="review-inbox-app">
    <h1>Review inbox</h1>
    <p v-if="loading" class="empty">Loading provisional candidates…</p>
    <p v-else-if="error" class="error">Failed to load inbox: {{ error }}</p>
    <p v-else-if="items.length === 0" class="empty">Inbox is empty — no provisional candidates awaiting review.</p>
    <ul v-else style="list-style: none; padding: 0;">
      <li v-for="item in items" :key="item.id" class="item">
        <header>
          <span class="real">{{ item.real }}</span>
          <span class="surrogate">→ {{ item.provisional_surrogate }}</span>
        </header>
        <p class="context">{{ item.context }}</p>
        <div class="actions">
          <button class="confirm" :disabled="item._busy" @click="confirm(item)">Confirm</button>
          <button class="reject"  :disabled="item._busy" @click="reject(item)">Reject</button>
        </div>
      </li>
    </ul>
  </div>

<script type="module">
import { createApp, ref, onMounted } from "https://unpkg.com/vue@3.4.27/dist/vue.esm-browser.prod.js";

const LIST_URL     = "/v1/management/review-inbox";
const CONFIRM_URL  = id => `/v1/management/review-inbox/${encodeURIComponent(id)}/confirm`;
const REJECT_URL   = id => `/v1/management/review-inbox/${encodeURIComponent(id)}/reject`;

createApp({
  setup() {
    const items   = ref([]);
    const loading = ref(true);
    const error   = ref(null);

    async function refresh() {
      loading.value = true;
      error.value = null;
      try {
        const r = await fetch(LIST_URL);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const body = await r.json();
        items.value = (body.items || []).map(i => ({ ...i, _busy: false }));
      } catch (e) {
        error.value = String(e);
      } finally {
        loading.value = false;
      }
    }

    async function act(item, url) {
      item._busy = true;
      try {
        const r = await fetch(url, { method: "POST" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        items.value = items.value.filter(i => i.id !== item.id);
      } catch (e) {
        error.value = String(e);
        item._busy = false;
      }
    }

    const confirm = item => act(item, CONFIRM_URL(item.id));
    const reject  = item => act(item, REJECT_URL(item.id));

    onMounted(refresh);

    return { items, loading, error, confirm, reject };
  }
}).mount("#review-inbox-app");
</script>
</body>
</html>
"""

_ORG_GRAPH_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Blindfold — Org Graph</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 0; color: #222; display: flex; flex-direction: column; height: 100vh; }
  header { padding: 0.75rem 1rem; background: #f5f5f5; border-bottom: 1px solid #ddd; display: flex; align-items: center; gap: 1rem; flex-shrink: 0; }
  h1 { font-size: 1.2rem; margin: 0; }
  select { padding: 0.3rem 0.5rem; border: 1px solid #ccc; border-radius: 3px; }
  #cy { flex: 1; background: #fff; }
  .error { color: #b00020; padding: 1rem; }
  #reveal-panel {
    position: fixed; bottom: 1rem; right: 1rem; background: #fff;
    border: 1px solid #ddd; border-radius: 6px; padding: 1rem; min-width: 260px;
    box-shadow: 0 2px 8px rgba(0,0,0,.15); display: none;
  }
  #reveal-panel.visible { display: block; }
  #reveal-panel h2 { font-size: 1rem; margin: 0 0 0.5rem; }
  .surrogate-label { font-family: ui-monospace, monospace; color: #555; }
  .real-value { font-weight: 600; color: #1a1a1a; }
  .reveal-error { color: #b00020; font-size: 0.85rem; }
  button.reveal-btn {
    background: #1f5fa6; color: white; border: none; padding: 0.35rem 0.75rem;
    border-radius: 3px; cursor: pointer; margin-top: 0.5rem;
  }
  button.reveal-btn[disabled] { opacity: 0.6; cursor: progress; }
  button.close-btn {
    background: none; border: none; cursor: pointer; float: right;
    font-size: 1rem; color: #666; padding: 0;
  }
</style>
</head>
<body>
  <div id="org-graph-app" style="display:contents">
    <header>
      <h1>Org graph</h1>
      <label for="ws-select">Workspace:</label>
      <select id="ws-select"></select>
      <span id="graph-error" class="error" style="display:none"></span>
    </header>
    <div id="cy"></div>
    <div id="reveal-panel">
      <button class="close-btn" id="reveal-close" title="Close">✕</button>
      <h2>Node</h2>
      <div>Kind: <span id="reveal-kind"></span></div>
      <div>Surrogate: <span class="surrogate-label" id="reveal-surrogate"></span></div>
      <div id="reveal-real-row" style="display:none">Real value: <span class="real-value" id="reveal-real"></span></div>
      <div id="reveal-error" class="reveal-error" style="display:none"></div>
      <button class="reveal-btn" id="reveal-btn">Reveal real value</button>
    </div>
  </div>

<script src="https://unpkg.com/cytoscape@3.29.2/dist/cytoscape.min.js"></script>
<script type="module">
// Endpoint base paths (ADR-0011 / issue #29).
// /v1/management/workspaces/<slug>/graph  — surrogate-space graph data
// /v1/management/surrogate/<surrogate>/real — re-identify (re-identifier role required)
const GRAPH_BASE    = "/v1/management/workspaces";
const REIDENTIFY_BASE = "/v1/management/surrogate";

const wsSelect    = document.getElementById("ws-select");
const graphError  = document.getElementById("graph-error");
const revealPanel = document.getElementById("reveal-panel");
const revealKind  = document.getElementById("reveal-kind");
const revealSur   = document.getElementById("reveal-surrogate");
const revealReal  = document.getElementById("reveal-real");
const revealRealRow = document.getElementById("reveal-real-row");
const revealErr   = document.getElementById("reveal-error");
const revealBtn   = document.getElementById("reveal-btn");
const revealClose = document.getElementById("reveal-close");

let cy = null;
let selectedNode = null;

// Initialise Cytoscape in the #cy container.
cy = cytoscape({
  container: document.getElementById("cy"),
  style: [
    { selector: "node[kind='person']",  style: { "background-color": "#4a90d9", label: "data(label)", "font-size": 12, color: "#fff", "text-valign": "center", "text-halign": "center", shape: "ellipse", width: 80, height: 40, "text-wrap": "wrap", "text-max-width": 70 } },
    { selector: "node[kind='term']",    style: { "background-color": "#8b5cf6", label: "data(label)", "font-size": 12, color: "#fff", "text-valign": "center", "text-halign": "center", shape: "roundrectangle", width: 90, height: 40, "text-wrap": "wrap", "text-max-width": 80 } },
    { selector: "node:selected",        style: { "border-width": 3, "border-color": "#f59e0b" } },
    { selector: "edge",                 style: { "curve-style": "bezier", "target-arrow-shape": "triangle", label: "data(relation)", "font-size": 10, "text-rotation": "autorotate", "line-color": "#aaa", "target-arrow-color": "#aaa" } },
  ],
  layout: { name: "cose", animate: false },
  elements: [],
});

cy.on("tap", "node", evt => {
  selectedNode = evt.target.data();
  revealKind.textContent = selectedNode.kind;
  revealSur.textContent  = selectedNode.label;
  revealRealRow.style.display = "none";
  revealReal.textContent = "";
  revealErr.style.display = "none";
  revealErr.textContent  = "";
  revealBtn.disabled = false;
  revealPanel.classList.add("visible");
});

revealClose.addEventListener("click", () => {
  revealPanel.classList.remove("visible");
  selectedNode = null;
});

revealBtn.addEventListener("click", async () => {
  if (!selectedNode) return;
  revealBtn.disabled = true;
  revealErr.style.display = "none";
  revealErr.textContent = "";
  const surrogate = selectedNode.label;
  const workspace = wsSelect.value;
  try {
    const r = await fetch(
      `${REIDENTIFY_BASE}/${encodeURIComponent(surrogate)}/real`,
      { headers: { "x-blindfold-workspace": workspace } }
    );
    if (r.status === 403) { revealErr.textContent = "Access denied — re-identifier role required."; revealErr.style.display = ""; revealBtn.disabled = false; return; }
    if (!r.ok) { revealErr.textContent = `Error ${r.status}`; revealErr.style.display = ""; revealBtn.disabled = false; return; }
    const body = await r.json();
    revealReal.textContent = body.real;
    revealRealRow.style.display = "";
  } catch (e) {
    revealErr.textContent = String(e);
    revealErr.style.display = "";
    revealBtn.disabled = false;
  }
});

// Workspace list: in this slice the workspace slug is entered or discovered
// from URL params; start with a default and let the user change it.
const params = new URLSearchParams(location.search);
const initWs = params.get("workspace") || "default";

function ensureOption(slug) {
  if (![...wsSelect.options].some(o => o.value === slug)) {
    const opt = document.createElement("option");
    opt.value = opt.textContent = slug;
    wsSelect.appendChild(opt);
  }
  wsSelect.value = slug;
}

async function loadGraph(workspace) {
  graphError.style.display = "none";
  try {
    const r = await fetch(`${GRAPH_BASE}/${encodeURIComponent(workspace)}/graph`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { nodes, edges } = await r.json();
    cy.elements().remove();
    cy.add([
      ...nodes.map(n => ({ group: "nodes", data: { id: n.id, label: n.label, kind: n.kind } })),
      ...edges.map(e => ({ group: "edges", data: { source: e.source, target: e.target, relation: e.relation } })),
    ]);
    cy.layout({ name: "cose", animate: false }).run();
  } catch (e) {
    graphError.textContent = String(e);
    graphError.style.display = "";
  }
}

ensureOption(initWs);
wsSelect.addEventListener("change", () => loadGraph(wsSelect.value));
loadGraph(initWs);
</script>
</body>
</html>
"""

_ENTITY_LIST_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Blindfold — Entity List</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 1rem; color: #222; }
  h1 { font-size: 1.3rem; margin-bottom: 0.5rem; }
  .toolbar { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; margin-bottom: 0.75rem; }
  .toolbar label { font-size: 0.9rem; }
  .toolbar input, .toolbar select { padding: 0.3rem 0.5rem; border: 1px solid #ccc; border-radius: 3px; font-size: 0.9rem; }
  .toolbar .search-box { display: flex; gap: 0.4rem; align-items: center; }
  .toolbar .search-box input { width: 220px; }
  button.search-btn { background: #1f5fa6; color: white; border: none; padding: 0.3rem 0.7rem; border-radius: 3px; cursor: pointer; font-size: 0.9rem; }
  button.search-btn[disabled] { opacity: 0.6; cursor: progress; }
  .locked-msg { color: #888; font-size: 0.85rem; font-style: italic; }
  .error { color: #b00020; font-size: 0.9rem; }
  .ceiling-msg { color: #666; background: #fffbe6; border: 1px solid #f0d080; border-radius: 4px; padding: 0.5rem 0.75rem; font-size: 0.9rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 2px solid #ddd; cursor: pointer; user-select: none; white-space: nowrap; }
  th:hover { background: #f5f5f5; }
  td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }
  tr.highlighted td { background: #fffbe6; }
  .kind-person { color: #1f5fa6; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }
  .kind-term { color: #8b5cf6; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }
  /* Inline surrogate rename (issue #33) */
  .surrogate-cell { font-family: ui-monospace, monospace; }
  .surrogate-text { cursor: pointer; border-bottom: 1px dashed #aaa; }
  .surrogate-text:hover { color: #1f5fa6; }
  .surrogate-input { font-family: ui-monospace, monospace; width: 14em; padding: 0.15rem 0.3rem; border: 1px solid #888; border-radius: 3px; font-size: 0.9rem; }
  .surrogate-input.error { border-color: #b00020; background: #fff0f0; }
  .rename-error { color: #b00020; font-size: 0.8rem; display: block; margin-top: 0.2rem; }
  .rename-warn { background: #fffbe6; border: 1px solid #f0d080; border-radius: 3px; padding: 0.4rem 0.6rem; font-size: 0.82rem; margin-top: 0.3rem; }
  .rename-warn label { display: flex; align-items: flex-start; gap: 0.3rem; cursor: pointer; }
  button.rename-save { background: #1f7a3f; color: white; border: none; padding: 0.2rem 0.5rem; border-radius: 3px; cursor: pointer; font-size: 0.82rem; margin-top: 0.3rem; }
  button.rename-save[disabled] { opacity: 0.6; cursor: progress; }
  button.rename-cancel { background: none; border: 1px solid #aaa; color: #555; padding: 0.2rem 0.5rem; border-radius: 3px; cursor: pointer; font-size: 0.82rem; margin-top: 0.3rem; margin-left: 0.3rem; }
  /* Edge chips (issue #33) */
  .edge-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .edge-chip { display: inline-flex; align-items: center; gap: 0.25rem; background: #f0f4ff; border: 1px solid #c0cfee; border-radius: 3px; padding: 0.1rem 0.35rem; font-size: 0.8rem; white-space: nowrap; }
  .chip-label { color: #333; font-family: ui-monospace, monospace; }
  button.chip-delete { background: none; border: none; color: #888; cursor: pointer; padding: 0 0.1rem; font-size: 0.85rem; line-height: 1; }
  button.chip-delete:hover { color: #b00020; }
  button.chip-retarget { background: none; border: none; color: #1f5fa6; cursor: pointer; padding: 0 0.1rem; font-size: 0.78rem; }
  button.chip-retarget:hover { text-decoration: underline; }
  /* Re-target picker (issue #33) — kind-constrained to term entities */
  .retarget-picker { display: flex; align-items: center; gap: 0.3rem; margin-top: 0.2rem; }
  .retarget-picker select { font-size: 0.82rem; padding: 0.15rem 0.3rem; border: 1px solid #ccc; border-radius: 3px; }
  button.retarget-confirm { background: #1f5fa6; color: white; border: none; padding: 0.2rem 0.5rem; border-radius: 3px; cursor: pointer; font-size: 0.82rem; }
  button.retarget-confirm[disabled] { opacity: 0.6; cursor: progress; }
  button.retarget-cancel { background: none; border: 1px solid #aaa; color: #555; padding: 0.2rem 0.5rem; border-radius: 3px; cursor: pointer; font-size: 0.82rem; }
  .retired { color: #888; font-size: 0.8rem; font-family: ui-monospace, monospace; }
  .reveal-badge {
    display: inline-block; background: #c8860a; color: #fff; border: none;
    border-radius: 3px; padding: 0.15rem 0.45rem; font-size: 0.78rem; cursor: pointer;
    margin-left: 0.4rem;
  }
  .reveal-badge.locked { background: #bbb; cursor: not-allowed; }
  .reveal-badge[disabled] { opacity: 0.6; cursor: progress; }
  .reveal-value { color: #1a1a1a; font-weight: 600; margin-left: 0.4rem; font-size: 0.88rem; }
  .empty { color: #666; font-style: italic; }
</style>
</head>
<body>
  <div id="entity-list-app">
    <h1>Entity list</h1>
    <div class="toolbar">
      <label for="ws-select">Workspace:</label>
      <select id="ws-select"></select>
      <label for="kind-filter">Kind:</label>
      <select id="kind-filter">
        <option value="">All</option>
        <option value="person">Person</option>
        <option value="term">Term</option>
      </select>
      <input id="surrogate-filter" type="text" placeholder="Filter by surrogate…" />
      <div class="search-box" id="search-box">
        <input id="real-name-input" type="text" placeholder="Real-name search…" />
        <button class="search-btn" id="search-btn">Search</button>
        <span class="locked-msg" id="search-locked" style="display:none">re-identifier role required</span>
      </div>
    </div>
    <div class="error" id="list-error" style="display:none"></div>
    <div class="ceiling-msg" id="ceiling-msg" style="display:none">
      More than 150 entities — narrow with surrogate filters or use real-name search to find specific records.
    </div>
    <p class="empty" id="loading-msg">Loading…</p>
    <table id="entity-table" style="display:none">
      <thead>
        <tr>
          <th data-col="active_surrogate">Surrogate ↕</th>
          <th data-col="kind">Kind ↕</th>
          <th data-col="employer">Edges</th>
          <th data-col="retired_surrogates">Retired surrogates</th>
          <th>Real value</th>
        </tr>
      </thead>
      <tbody id="entity-tbody"></tbody>
    </table>
  </div>

<script type="module">
// Endpoint base paths (ADR-0011 / issue #32 / issue #33).
// /v1/management/workspaces/<slug>/entities          — surrogate-space entity list
// /v1/management/workspaces/<slug>/entities/search   — real-name search (re-identifier role)
// /v1/management/workspaces/<slug>/relationships     — edge CRUD (no role required, #27)
// /v1/management/entities/<id>/surrogate             — inline surrogate rename (admin, PATCH, #28)
// /v1/management/surrogate/<surrogate>/real          — re-identify (re-identifier role, ADR-0015)
const ENTITIES_BASE   = "/v1/management/workspaces";
const MANAGEMENT_ENTITIES_BASE = "/v1/management/entities";
const REIDENTIFY_BASE = "/v1/management/surrogate";

const ENTITY_LIST_CEILING = 150;

const wsSelect        = document.getElementById("ws-select");
const kindFilter      = document.getElementById("kind-filter");
const surrogateFilter = document.getElementById("surrogate-filter");
const realNameInput   = document.getElementById("real-name-input");
const searchBtn       = document.getElementById("search-btn");
const searchLocked    = document.getElementById("search-locked");
const listError       = document.getElementById("list-error");
const ceilingMsg      = document.getElementById("ceiling-msg");
const loadingMsg      = document.getElementById("loading-msg");
const entityTable     = document.getElementById("entity-table");
const entityTbody     = document.getElementById("entity-tbody");

let allRows    = [];
let highlighted = new Set();
let sortCol    = "active_surrogate";
let sortAsc    = true;
let canSearch  = false;

async function detectSearchCapability(workspace) {
  try {
    const r = await fetch(
      `${ENTITIES_BASE}/${encodeURIComponent(workspace)}/entities/search?q=__probe__`,
      { headers: { "x-blindfold-workspace": workspace } }
    );
    canSearch = (r.status !== 403);
  } catch (_) {
    canSearch = false;
  }
  searchLocked.style.display = canSearch ? "none" : "";
  searchBtn.disabled = !canSearch;
  realNameInput.disabled = !canSearch;
}

async function loadEntities(workspace) {
  listError.style.display = "none";
  ceilingMsg.style.display = "none";
  entityTable.style.display = "none";
  loadingMsg.style.display = "";
  highlighted.clear();
  try {
    const r = await fetch(`${ENTITIES_BASE}/${encodeURIComponent(workspace)}/entities`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    allRows = data.entities || [];
    if (allRows.length > ENTITY_LIST_CEILING) {
      ceilingMsg.style.display = "";
      allRows = [];
    }
  } catch (e) {
    listError.textContent = String(e);
    listError.style.display = "";
  } finally {
    loadingMsg.style.display = "none";
  }
  await detectSearchCapability(workspace);
  renderTable();
}

function getEdgeSurrogate(row, relation) {
  const edge = (row.edges || []).find(e => e.relation === relation);
  return edge ? edge.other_surrogate : "";
}

function sortedAndFiltered() {
  const kindVal = kindFilter.value;
  const surVal  = surrogateFilter.value.toLowerCase();
  let rows = allRows.filter(r => {
    if (kindVal && r.kind !== kindVal) return false;
    if (surVal && !r.active_surrogate.toLowerCase().includes(surVal)) return false;
    return true;
  });
  rows.sort((a, b) => {
    let va = "", vb = "";
    if (sortCol === "active_surrogate") { va = a.active_surrogate; vb = b.active_surrogate; }
    else if (sortCol === "kind") { va = a.kind; vb = b.kind; }
    else if (sortCol === "employer") { va = getEdgeSurrogate(a, "employer"); vb = getEdgeSurrogate(b, "employer"); }
    else if (sortCol === "retired_surrogates") { va = (a.retired_surrogates || []).join(","); vb = (b.retired_surrogates || []).join(","); }
    const cmp = va.localeCompare(vb);
    return sortAsc ? cmp : -cmp;
  });
  return rows;
}

// ---------------------------------------------------------------------------
// Inline surrogate rename (issue #33)
// Click on the surrogate text to open an input field. PATCH /entities/{id}/surrogate.
// 409 → hard reject (red inline field error); 200 with dependents → soft warn + ack.
// Requires admin role on the workspace; does NOT require re-identifier.
// ---------------------------------------------------------------------------

function makeSurrogateCell(row) {
  const cell = document.createElement("td");
  cell.className = "surrogate-cell";

  const textSpan = document.createElement("span");
  textSpan.className = "surrogate-text";
  textSpan.textContent = row.active_surrogate;
  textSpan.title = "Click to rename surrogate";
  cell.appendChild(textSpan);

  textSpan.addEventListener("click", () => openRenameForm(row, cell, textSpan));
  return cell;
}

function openRenameForm(row, cell, textSpan) {
  // Avoid double-opening
  if (cell.querySelector(".surrogate-input")) return;

  textSpan.style.display = "none";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "surrogate-input";
  input.value = row.active_surrogate;
  cell.appendChild(input);

  const errorSpan = document.createElement("span");
  errorSpan.className = "rename-error";
  errorSpan.style.display = "none";
  cell.appendChild(errorSpan);

  // Soft-warn container (shown when dependents are returned)
  const warnDiv = document.createElement("div");
  warnDiv.className = "rename-warn";
  warnDiv.style.display = "none";
  const warnLabel = document.createElement("label");
  const warnCheck = document.createElement("input");
  warnCheck.type = "checkbox";
  const warnText = document.createTextNode(" Renaming will affect dependent entities — I acknowledge.");
  warnLabel.appendChild(warnCheck);
  warnLabel.appendChild(warnText);
  warnDiv.appendChild(warnLabel);
  cell.appendChild(warnDiv);

  const saveBtn = document.createElement("button");
  saveBtn.className = "rename-save";
  saveBtn.textContent = "Save";
  cell.appendChild(saveBtn);

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "rename-cancel";
  cancelBtn.textContent = "Cancel";
  cell.appendChild(cancelBtn);

  // Pending rename result when dependents need ack
  let pendingResult = null;

  async function attemptRename() {
    const newSurrogate = input.value.trim();
    if (!newSurrogate || newSurrogate === row.active_surrogate) { closeRenameForm(); return; }

    // If dependents shown and not acknowledged, block
    if (warnDiv.style.display !== "none" && !warnCheck.checked) {
      errorSpan.textContent = "Acknowledge the dependent warning before saving.";
      errorSpan.style.display = "";
      return;
    }

    saveBtn.disabled = true;
    errorSpan.style.display = "none";
    input.classList.remove("error");

    const workspace = wsSelect.value;
    try {
      const r = await fetch(
        `${MANAGEMENT_ENTITIES_BASE}/${encodeURIComponent(row.entity_id)}/surrogate`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "x-blindfold-identity": "" },
          body: JSON.stringify({ workspace, new_surrogate: newSurrogate }),
        }
      );
      if (r.status === 409) {
        const body = await r.json();
        input.classList.add("error");
        errorSpan.textContent = "Collision: " + (body.detail || "surrogate already in use");
        errorSpan.style.display = "";
        saveBtn.disabled = false;
        return;
      }
      if (!r.ok) {
        errorSpan.textContent = `Error ${r.status}`;
        errorSpan.style.display = "";
        saveBtn.disabled = false;
        return;
      }
      const body = await r.json();
      pendingResult = body;

      if (body.inconsistent_dependents && body.inconsistent_dependents.length > 0) {
        // Soft warn: show banner, require ack before the rename is considered complete
        warnDiv.style.display = "";
        warnCheck.checked = false;
        saveBtn.disabled = false;
        // Update the in-memory row so the next ack-save goes through as a no-op rename
        row.active_surrogate = newSurrogate;
        input.value = newSurrogate;
        return;
      }

      // Clean rename: update row in memory and close
      row.active_surrogate = newSurrogate;
      closeRenameForm(newSurrogate);
    } catch (e) {
      errorSpan.textContent = String(e);
      errorSpan.style.display = "";
      saveBtn.disabled = false;
    }
  }

  function closeRenameForm(newValue) {
    textSpan.textContent = newValue || row.active_surrogate;
    textSpan.style.display = "";
    [input, errorSpan, warnDiv, saveBtn, cancelBtn].forEach(el => cell.removeChild(el));
  }

  saveBtn.addEventListener("click", attemptRename);
  cancelBtn.addEventListener("click", () => closeRenameForm());
  input.addEventListener("keydown", e => { if (e.key === "Enter") attemptRename(); if (e.key === "Escape") closeRenameForm(); });

  input.focus();
  input.select();
}

// ---------------------------------------------------------------------------
// Edge chips: one chip per edge for outbound employer/subsidiary_of relations.
// Each chip has × (delete) and a retarget button (re-target). No primary designation.
// DELETE /workspaces/{slug}/relationships/{edge_id}
// POST   /workspaces/{slug}/relationships  (re-target: delete old + create with new target)
// ---------------------------------------------------------------------------

function makeEdgesCell(row) {
  const cell = document.createElement("td");
  const container = document.createElement("div");
  container.className = "edge-chips";
  cell.appendChild(container);

  const outboundEdges = (row.edges || []).filter(e => e.direction === "outbound");

  if (outboundEdges.length === 0) return cell;

  for (const edge of outboundEdges) {
    appendEdgeChip(container, row, edge);
  }
  return cell;
}

function appendEdgeChip(container, row, edge) {
  const chip = document.createElement("span");
  chip.className = "edge-chip";
  chip.dataset.edgeId = edge.edge_id;

  const label = document.createElement("span");
  label.className = "chip-label";
  label.textContent = `${edge.relation}: ${edge.other_surrogate}`;
  chip.appendChild(label);

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "chip-delete";
  deleteBtn.textContent = "×";
  deleteBtn.title = `Remove ${edge.relation} edge`;
  deleteBtn.addEventListener("click", () => deleteEdge(container, chip, row, edge));
  chip.appendChild(deleteBtn);

  const retargetBtn = document.createElement("button");
  retargetBtn.className = "chip-retarget";
  retargetBtn.textContent = "↔";
  retargetBtn.title = `Re-target ${edge.relation} edge`;
  retargetBtn.addEventListener("click", () => retargetEdge(container, chip, row, edge));
  chip.appendChild(retargetBtn);

  container.appendChild(chip);
}

async function deleteEdge(container, chip, row, edge) {
  const workspace = wsSelect.value;
  try {
    const r = await fetch(
      `${ENTITIES_BASE}/${encodeURIComponent(workspace)}/relationships/${encodeURIComponent(edge.edge_id)}`,
      { method: "DELETE" }
    );
    if (!r.ok) { alert(`Delete failed: HTTP ${r.status}`); return; }
    // Remove chip from the in-memory row and from the DOM
    row.edges = (row.edges || []).filter(e => e.edge_id !== edge.edge_id);
    container.removeChild(chip);
  } catch (e) {
    alert(String(e));
  }
}

// Re-target: show a kind-constrained term picker below the chip.
// Term is the only valid target kind for employer and subsidiary_of (controlled vocab, #27).
function retargetEdge(container, chip, row, edge) {
  // Avoid opening a second picker on the same chip
  if (chip.querySelector(".retarget-picker")) return;

  const pickerRow = document.createElement("div");
  pickerRow.className = "retarget-picker";

  const sel = document.createElement("select");
  // Kind-constrained: only term entities are valid targets
  const termRows = allRows.filter(r => r.kind === "term" && r.entity_id !== row.entity_id);
  if (termRows.length === 0) {
    const opt = document.createElement("option");
    opt.textContent = "No term entities available";
    sel.appendChild(opt);
    sel.disabled = true;
  } else {
    for (const t of termRows) {
      const opt = document.createElement("option");
      opt.value = t.entity_id;
      opt.textContent = t.active_surrogate;
      if (t.entity_id === edge.other_entity_id) opt.selected = true;
      sel.appendChild(opt);
    }
  }
  pickerRow.appendChild(sel);

  const confirmBtn = document.createElement("button");
  confirmBtn.className = "retarget-confirm";
  confirmBtn.textContent = "Apply";
  pickerRow.appendChild(confirmBtn);

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "retarget-cancel";
  cancelBtn.textContent = "Cancel";
  pickerRow.appendChild(cancelBtn);

  chip.appendChild(pickerRow);

  cancelBtn.addEventListener("click", () => chip.removeChild(pickerRow));

  confirmBtn.addEventListener("click", async () => {
    const newTargetId = sel.value;
    if (!newTargetId || newTargetId === edge.other_entity_id) { chip.removeChild(pickerRow); return; }

    confirmBtn.disabled = true;
    const workspace = wsSelect.value;

    // Step 1: delete the old edge
    try {
      const delR = await fetch(
        `${ENTITIES_BASE}/${encodeURIComponent(workspace)}/relationships/${encodeURIComponent(edge.edge_id)}`,
        { method: "DELETE" }
      );
      if (!delR.ok) { alert(`Delete failed: HTTP ${delR.status}`); confirmBtn.disabled = false; return; }
    } catch (e) { alert(String(e)); confirmBtn.disabled = false; return; }

    // Step 2: create the new edge to the re-targeted term
    const newTargetRow = allRows.find(r => r.entity_id === newTargetId);
    try {
      const createR = await fetch(
        `${ENTITIES_BASE}/${encodeURIComponent(workspace)}/relationships`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source_kind: row.kind,
            source_id: row.entity_id,
            relation: edge.relation,
            target_kind: edge.target_kind,
            target_id: newTargetId,
          }),
        }
      );
      if (!createR.ok) { alert(`Create failed: HTTP ${createR.status}`); confirmBtn.disabled = false; return; }
      const newEdgeData = await createR.json();

      // Update in-memory row: swap old edge for new
      const newEdge = {
        edge_id: newEdgeData.id,
        relation: edge.relation,
        direction: "outbound",
        other_surrogate: newTargetRow ? newTargetRow.active_surrogate : newTargetId,
        other_entity_id: newTargetId,
        target_kind: edge.target_kind,
      };
      row.edges = (row.edges || []).filter(e => e.edge_id !== edge.edge_id).concat([newEdge]);

      // Refresh this chip's label and remove picker
      const label = chip.querySelector(".chip-label");
      if (label) label.textContent = `${newEdge.relation}: ${newEdge.other_surrogate}`;
      chip.dataset.edgeId = newEdge.edge_id;
      // Update the chip's delete/retarget handlers by replacing the chip
      chip.removeChild(pickerRow);
      // Swap edge reference on delete/retarget so future clicks use the new edge
      Object.assign(edge, newEdge);
    } catch (e) { alert(String(e)); confirmBtn.disabled = false; }
  });
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

function renderTable() {
  const rows = sortedAndFiltered();
  entityTbody.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (highlighted.has(row.entity_id)) tr.classList.add("highlighted");

    const retired = (row.retired_surrogates || []).join(", ");

    // Surrogate cell: inline rename (issue #33, requires admin)
    tr.appendChild(makeSurrogateCell(row));

    // Kind cell (immutable; no delete-entity action per issue #33)
    const kindTd = document.createElement("td");
    kindTd.innerHTML = `<span class="kind-${esc(row.kind)}">${esc(row.kind)}</span>`;
    tr.appendChild(kindTd);

    // Edges cell: chips for all outbound edges (one per edge, no primary, issue #33)
    tr.appendChild(makeEdgesCell(row));

    // Retired surrogates
    const retiredTd = document.createElement("td");
    retiredTd.className = "retired";
    retiredTd.textContent = retired;
    tr.appendChild(retiredTd);

    // Real value (reveal badge)
    const revealTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "reveal-badge" + (canSearch ? "" : " locked");
    btn.textContent = canSearch ? "Reveal" : "locked";
    btn.disabled = !canSearch;
    btn.title = canSearch ? "This will be logged" : "re-identifier role required";
    if (canSearch) {
      btn.addEventListener("click", () => revealRow(row, btn, revealTd));
    }
    revealTd.appendChild(btn);
    tr.appendChild(revealTd);

    entityTbody.appendChild(tr);
  }
  entityTable.style.display = rows.length > 0 ? "" : "none";
  if (rows.length === 0 && allRows.length === 0 && ceilingMsg.style.display === "none") {
    loadingMsg.textContent = "No entities in this workspace.";
    loadingMsg.style.display = "";
  }
}

async function revealRow(row, btn, td) {
  if (!confirm("Revealing the real value will be logged. Continue?")) return;
  btn.disabled = true;
  const workspace = wsSelect.value;
  try {
    const r = await fetch(
      `${REIDENTIFY_BASE}/${encodeURIComponent(row.active_surrogate)}/real`,
      { headers: { "x-blindfold-workspace": workspace } }
    );
    if (r.status === 403) { alert("Access denied — re-identifier role required."); btn.disabled = false; return; }
    if (!r.ok) { alert(`Error ${r.status}`); btn.disabled = false; return; }
    const body = await r.json();
    const val = document.createElement("span");
    val.className = "reveal-value";
    val.textContent = body.real;
    btn.replaceWith(val);
  } catch (e) {
    alert(String(e));
    btn.disabled = false;
  }
}

searchBtn.addEventListener("click", async () => {
  const q = realNameInput.value.trim();
  if (!q) return;
  searchBtn.disabled = true;
  const workspace = wsSelect.value;
  highlighted.clear();
  try {
    const r = await fetch(
      `${ENTITIES_BASE}/${encodeURIComponent(workspace)}/entities/search?q=${encodeURIComponent(q)}`,
      { headers: { "x-blindfold-workspace": workspace } }
    );
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    for (const hit of (data.hits || [])) highlighted.add(hit.entity_id);
    renderTable();
  } catch (e) {
    listError.textContent = String(e);
    listError.style.display = "";
  } finally {
    searchBtn.disabled = false;
  }
});

kindFilter.addEventListener("input", renderTable);
surrogateFilter.addEventListener("input", renderTable);

document.querySelectorAll("th[data-col]").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (sortCol === col) { sortAsc = !sortAsc; }
    else { sortCol = col; sortAsc = true; }
    renderTable();
  });
});

const params = new URLSearchParams(location.search);
const initWs = params.get("workspace") || "default";

function ensureOption(slug) {
  if (![...wsSelect.options].some(o => o.value === slug)) {
    const opt = document.createElement("option");
    opt.value = opt.textContent = slug;
    wsSelect.appendChild(opt);
  }
  wsSelect.value = slug;
}

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

ensureOption(initWs);
wsSelect.addEventListener("change", () => loadEntities(wsSelect.value));
loadEntities(initWs);
</script>
</body>
</html>
"""

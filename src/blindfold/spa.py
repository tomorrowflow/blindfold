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
ENTITY_LIST_MERGE_ENDPOINT = "/v1/management/workspaces"
REIDENTIFY_ENDPOINT = "/v1/management/surrogate"
MERGE_ENDPOINT = "/v1/management/entities/merge"
EDIT_SURROGATE_ENDPOINT = "/v1/management/entities"


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
  *, *::before, *::after { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 0; color: #222; display: flex; flex-direction: column; height: 100vh; }
  header { padding: 0.6rem 1rem; background: #f5f5f5; border-bottom: 1px solid #ddd; display: flex; align-items: center; gap: 1rem; flex-shrink: 0; flex-wrap: wrap; }
  h1 { font-size: 1.2rem; margin: 0; }
  select { padding: 0.3rem 0.5rem; border: 1px solid #ccc; border-radius: 3px; }
  #main { flex: 1; display: flex; overflow: hidden; }
  #cy { flex: 1; background: #fff; }

  /* Surrogate inspector panel */
  #inspector { width: 300px; border-left: 1px solid #ddd; overflow-y: auto; padding: 1rem; display: none; flex-shrink: 0; }
  #inspector h2 { font-size: 1rem; margin: 0 0 0.75rem; }
  .insp-row { margin-bottom: 0.5rem; font-size: 0.9rem; }
  .surrogate-label { font-family: ui-monospace, monospace; color: #444; }
  #insp-rename-input { width: 100%; padding: 0.35rem; border: 1px solid #ccc; border-radius: 3px; margin-top: 0.25rem; }
  #insp-rename-input.collision { border-color: #b00020; background: #fff0f0; }
  #insp-rename-error { color: #b00020; font-size: 0.82rem; margin-top: 0.2rem; display: none; }
  #insp-rename-warning { background: #f1f5f9; border: 1px solid #94a3b8; border-radius: 4px; padding: 0.6rem; margin-top: 0.5rem; font-size: 0.85rem; display: none; }
  #insp-rename-warning p { margin: 0 0 0.4rem; }

  /* Per-node reveal badge (ochre, positioned over the canvas) */
  #reveal-badge { position: fixed; z-index: 5; display: none; }
  #reveal-badge-btn { background: #d97706; color: white; border: none; border-radius: 4px; padding: 0.15rem 0.45rem; font-size: 0.72rem; cursor: pointer; }
  #reveal-badge-locked { background: #6b7280; color: white; border-radius: 4px; padding: 0.15rem 0.45rem; font-size: 0.72rem; display: none; }

  /* Dialogs */
  .dialog-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: none; align-items: center; justify-content: center; z-index: 20; }
  .dialog-backdrop.open { display: flex; }
  .dialog-box { background: #fff; border-radius: 8px; padding: 1.5rem; min-width: 380px; max-width: 640px; box-shadow: 0 8px 24px rgba(0,0,0,.2); }
  .dialog-box h2 { margin: 0 0 0.5rem; font-size: 1.1rem; }

  /* Merge dialog */
  .merge-cols { display: flex; gap: 0.75rem; margin: 0.75rem 0; }
  .merge-col { flex: 1; border: 1px solid #ddd; border-radius: 4px; padding: 0.65rem; }
  .merge-col h3 { margin: 0 0 0.4rem; font-size: 0.9rem; }
  .merge-col-winner h3 { color: #1f7a3f; }
  .merge-col-retired h3 { color: #b00020; }
  .merge-swap-area { display: flex; align-items: center; }

  /* Buttons */
  .btn-primary { background: #1f5fa6; color: white; border: none; padding: 0.35rem 0.8rem; border-radius: 3px; cursor: pointer; font-size: 0.9rem; }
  .btn-secondary { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; padding: 0.35rem 0.8rem; border-radius: 3px; cursor: pointer; font-size: 0.9rem; }
  .btn-danger { background: #b00020; color: white; border: none; padding: 0.35rem 0.8rem; border-radius: 3px; cursor: pointer; font-size: 0.9rem; }
  button[disabled] { opacity: 0.55; cursor: not-allowed; }
  .btn-row { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1rem; }

  .error-text { color: #b00020; font-size: 0.85rem; }
  .real-value { font-weight: 600; color: #1a1a1a; }
</style>
</head>
<body>
<div id="org-graph-app" style="display:contents">
  <header>
    <h1>Org graph</h1>
    <label for="ws-select">Workspace:</label>
    <select id="ws-select"></select>
    <span id="graph-error" class="error-text" style="display:none"></span>
  </header>

  <div id="main">
    <div id="cy"></div>

    <!-- Surrogate inspector panel — shown when a node is selected (issues #28, #30) -->
    <div id="inspector">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem">
        <h2 style="margin:0">Inspector</h2>
        <button class="btn-secondary" id="inspector-close" style="padding:0.15rem 0.45rem">✕</button>
      </div>
      <div class="insp-row"><span>Kind:</span> <span id="insp-kind"></span></div>
      <div class="insp-row"><span>Surrogate:</span> <span class="surrogate-label" id="insp-surrogate"></span></div>
      <div class="insp-row"><span>Variations:</span> <span id="insp-variations" style="color:#555; font-size:0.85rem"></span></div>
      <div class="insp-row">
        <span>Edges:</span>
        <ul id="insp-edges" style="margin:0.2rem 0 0; padding-left:1.1rem; font-size:0.85rem"></ul>
      </div>
      <hr style="margin:0.75rem 0" />
      <div>
        <label for="insp-rename-input" style="font-size:0.9rem">Rename surrogate:</label>
        <input id="insp-rename-input" type="text" placeholder="New surrogate value" />
        <div id="insp-rename-error"></div>
        <div id="insp-rename-warning">
          <p>Some dependent entities may have inconsistent coherent-world surrogates after this rename. Fix them individually (no cascade this slice).</p>
          <label style="display:flex; gap:0.4rem; align-items:center; margin-bottom:0.4rem">
            <input type="checkbox" id="insp-ack-checkbox" /> Acknowledge
          </label>
          <button class="btn-primary" id="insp-rename-ack-btn" disabled>Acknowledge &amp; rename</button>
        </div>
        <div style="margin-top:0.5rem; display:flex; gap:0.5rem">
          <button class="btn-primary" id="insp-rename-btn">Rename</button>
          <button class="btn-secondary" id="insp-draw-edge-btn">Draw edge</button>
          <button class="btn-danger" id="insp-delete-edge-btn" style="display:none">Delete edge</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Per-node reveal badge (ochre overlay, positions at selected node corner via JS) -->
  <div id="reveal-badge">
    <button id="reveal-badge-btn">Reveal</button>
    <span id="reveal-badge-locked">locked</span>
  </div>

  <!-- Merge confirm dialog (drag-to-merge, issue #30) -->
  <div id="merge-dialog-backdrop" class="dialog-backdrop">
    <div class="dialog-box" id="merge-dialog">
      <h2>Merge entities</h2>
      <p style="margin:0 0 0.5rem; color:#555; font-size:0.9rem">
        The <strong>Survivor</strong> absorbs the <strong>Retired</strong> entity.
        All edges and variations re-home to the Survivor. This action requires the admin role.
      </p>
      <div class="merge-cols">
        <div class="merge-col merge-col-winner">
          <h3>Survivor</h3>
          <div class="surrogate-label" id="merge-winner-surrogate"></div>
          <div style="font-size:0.8rem; color:#555; margin-top:0.2rem" id="merge-winner-kind"></div>
          <div class="real-value" id="merge-winner-reveal-result" style="font-size:0.85rem; margin-top:0.3rem; display:none"></div>
          <div class="error-text" id="merge-winner-reveal-error" style="display:none"></div>
          <button class="btn-secondary" id="merge-winner-reveal" style="margin-top:0.5rem; font-size:0.8rem">Reveal real value</button>
        </div>
        <div class="merge-swap-area">
          <button class="btn-secondary" id="merge-swap-btn" title="Swap Survivor and Retired">&#8644; Swap</button>
        </div>
        <div class="merge-col merge-col-retired">
          <h3>Retired</h3>
          <div class="surrogate-label" id="merge-loser-surrogate"></div>
          <div style="font-size:0.8rem; color:#555; margin-top:0.2rem" id="merge-loser-kind"></div>
          <div class="real-value" id="merge-loser-reveal-result" style="font-size:0.85rem; margin-top:0.3rem; display:none"></div>
          <div class="error-text" id="merge-loser-reveal-error" style="display:none"></div>
          <button class="btn-secondary" id="merge-loser-reveal" style="margin-top:0.5rem; font-size:0.8rem">Reveal real value</button>
        </div>
      </div>
      <div id="merge-error" class="error-text" style="display:none; margin-bottom:0.4rem"></div>
      <div class="btn-row">
        <button class="btn-secondary" id="merge-cancel-btn">Cancel</button>
        <button class="btn-primary" id="merge-confirm-btn">Confirm merge</button>
      </div>
    </div>
  </div>

  <!-- Edge type picker dialog (draw edge gesture, issue #30) -->
  <div id="edge-picker-backdrop" class="dialog-backdrop">
    <div class="dialog-box">
      <h2>Add relationship</h2>
      <p id="edge-direction-label" style="font-family:ui-monospace,monospace; color:#444; margin:0 0 1rem">Source &#8594; Target</p>
      <label style="font-size:0.9rem">Relationship type:
        <select id="edge-type-select" style="margin-left:0.4rem">
          <option value="employer">employer</option>
          <option value="subsidiary_of">subsidiary_of</option>
        </select>
      </label>
      <div id="edge-picker-error" class="error-text" style="display:none; margin-top:0.4rem"></div>
      <div class="btn-row">
        <button class="btn-secondary" id="edge-cancel-btn">Cancel</button>
        <button class="btn-primary" id="edge-confirm-btn">Add edge</button>
      </div>
    </div>
  </div>

  <!-- Reveal audit-confirm dialog (per-node badge, also in merge dialog) -->
  <div id="reveal-audit-backdrop" class="dialog-backdrop">
    <div class="dialog-box">
      <h2>Reveal real value</h2>
      <p style="color:#555">This re-identification will be logged as an audit event.</p>
      <div class="btn-row">
        <button class="btn-secondary" id="reveal-confirm-no">Cancel</button>
        <button class="btn-primary" id="reveal-confirm-yes">Proceed</button>
      </div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/cytoscape@3.29.2/dist/cytoscape.min.js"></script>
<script src="https://unpkg.com/cytoscape-edgehandles@4.0.1/cytoscape-edgehandles.js"></script>
<script type="module">
// Management-API seam endpoint constants (ADR-0011 / issues #26-#29 / #30).
// GRAPH_BASE: GET {slug}/graph — surrogate-space graph data (no audit event)
//             POST/DELETE {slug}/relationships — edge CRUD (controlled vocabulary)
// REIDENTIFY_BASE: GET {surrogate}/real — re-identify (re-identifier role, audited, ADR-0015)
// MERGE_URL: POST — merge two same-kind entities (admin role, ADR-0016)
// ENTITIES_BASE: PATCH {id}/surrogate — rename surrogate (admin role, issue #28)
const GRAPH_BASE      = "/v1/management/workspaces";
const REIDENTIFY_BASE = "/v1/management/surrogate";
const MERGE_URL       = "/v1/management/entities/merge";
const ENTITIES_BASE   = "/v1/management/entities";

// relationships sub-path for edge CRUD: GRAPH_BASE + "/{slug}/relationships"
const RELATIONSHIPS_PATH = "relationships";

// Text shown in the reveal badge when the caller lacks the re-identifier role.
const REVEAL_LOCKED_LABEL = "locked";

// ---------------------------------------------------------------------------
// Element references
// ---------------------------------------------------------------------------
const wsSelect      = document.getElementById("ws-select");
const graphError    = document.getElementById("graph-error");
const inspector     = document.getElementById("inspector");
const inspKind      = document.getElementById("insp-kind");
const inspSurrogate = document.getElementById("insp-surrogate");
const inspVariations = document.getElementById("insp-variations");
const inspEdges     = document.getElementById("insp-edges");
const inspRenameInput = document.getElementById("insp-rename-input");
const inspRenameError = document.getElementById("insp-rename-error");
const inspRenameWarning = document.getElementById("insp-rename-warning");
const inspAckCheckbox = document.getElementById("insp-ack-checkbox");
const inspRenameAckBtn = document.getElementById("insp-rename-ack-btn");
const inspRenameBtn = document.getElementById("insp-rename-btn");
const inspCloseBtn  = document.getElementById("inspector-close");
const inspDrawEdgeBtn = document.getElementById("insp-draw-edge-btn");
const inspDeleteEdgeBtn = document.getElementById("insp-delete-edge-btn");
const revealBadge   = document.getElementById("reveal-badge");
const revealBadgeBtn = document.getElementById("reveal-badge-btn");
const revealBadgeLocked = document.getElementById("reveal-badge-locked");
const mergeDlgBackdrop = document.getElementById("merge-dialog-backdrop");
const mergeWinnerSur = document.getElementById("merge-winner-surrogate");
const mergeWinnerKind = document.getElementById("merge-winner-kind");
const mergeWinnerReveal = document.getElementById("merge-winner-reveal");
const mergeWinnerRevealResult = document.getElementById("merge-winner-reveal-result");
const mergeWinnerRevealError = document.getElementById("merge-winner-reveal-error");
const mergeLoserSur  = document.getElementById("merge-loser-surrogate");
const mergeLoserKind = document.getElementById("merge-loser-kind");
const mergeLoserReveal = document.getElementById("merge-loser-reveal");
const mergeLoserRevealResult = document.getElementById("merge-loser-reveal-result");
const mergeLoserRevealError = document.getElementById("merge-loser-reveal-error");
const mergeSwapBtn  = document.getElementById("merge-swap-btn");
const mergeConfirmBtn = document.getElementById("merge-confirm-btn");
const mergeCancelBtn  = document.getElementById("merge-cancel-btn");
const mergeError    = document.getElementById("merge-error");
const edgePickerBackdrop = document.getElementById("edge-picker-backdrop");
const edgeDirectionLabel = document.getElementById("edge-direction-label");
const edgeTypeSelect = document.getElementById("edge-type-select");
const edgePickerError = document.getElementById("edge-picker-error");
const edgeConfirmBtn = document.getElementById("edge-confirm-btn");
const edgeCancelBtn  = document.getElementById("edge-cancel-btn");
const revealAuditBackdrop = document.getElementById("reveal-audit-backdrop");
const revealConfirmYes = document.getElementById("reveal-confirm-yes");
const revealConfirmNo  = document.getElementById("reveal-confirm-no");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let cy = null;
let currentWorkspace = "default";
let selectedNode = null;   // cytoscape node element
let selectedEdge = null;   // cytoscape edge element
let mergeWinner = null;    // { id, label, kind }
let mergeLoser  = null;    // { id, label, kind }
let edgePendingSource = null;  // node element for edge draw
let edgePendingTarget = null;  // node element for edge draw
let edgeDrawMode = false;
let revealTarget = null;   // { surrogate, callback }

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function showGraphError(msg) {
  graphError.textContent = msg;
  graphError.style.display = "";
}

function hideGraphError() {
  graphError.style.display = "none";
}

function openDialog(backdrop) {
  backdrop.classList.add("open");
}

function closeDialog(backdrop) {
  backdrop.classList.remove("open");
}

// ---------------------------------------------------------------------------
// Inspector panel
// ---------------------------------------------------------------------------

function showInspector(nodeData, nodeElem) {
  selectedNode = nodeElem;
  inspKind.textContent = nodeData.kind;
  inspSurrogate.textContent = nodeData.label;
  inspVariations.textContent = "(reveal to view real-name variations)";
  inspEdges.innerHTML = "";
  // List edges for this node from the graph
  const edges = cy.edges().filter(
    e => e.data("source") === nodeData.id || e.data("target") === nodeData.id
  );
  edges.forEach(e => {
    const li = document.createElement("li");
    const d = e.data();
    const other = d.source === nodeData.id ? d.target : d.source;
    const dir = d.source === nodeData.id ? "→" : "←";
    li.textContent = dir + " " + d.relation + " (edge id: " + d.edgeId + ")";
    inspEdges.appendChild(li);
  });
  // Reset rename form
  inspRenameInput.value = "";
  inspRenameInput.classList.remove("collision");
  inspRenameError.style.display = "none";
  inspRenameWarning.style.display = "none";
  inspAckCheckbox.checked = false;
  inspRenameAckBtn.disabled = true;
  inspector.style.display = "";
  // Position reveal badge at the node corner
  positionRevealBadge(nodeElem);
}

function hideInspector() {
  inspector.style.display = "none";
  revealBadge.style.display = "none";
  selectedNode = null;
}

function positionRevealBadge(nodeElem) {
  const rp = nodeElem.renderedPosition();
  const off = nodeElem.renderedWidth() / 2;
  revealBadge.style.left = (rp.x + off - 10) + "px";
  revealBadge.style.top  = (rp.y - nodeElem.renderedHeight() / 2 - 10) + "px";
  revealBadge.style.display = "";
  revealBadgeBtn.style.display = "";
  revealBadgeLocked.style.display = "none";
}

// ---------------------------------------------------------------------------
// Rename surrogate (surrogate inspector, issue #28 / #30)
// ---------------------------------------------------------------------------

async function submitRename(acknowledged) {
  const newSurrogate = inspRenameInput.value.trim();
  if (!newSurrogate) return;
  const entityId = selectedNode ? selectedNode.data("id") : null;
  if (!entityId) return;

  inspRenameBtn.disabled = true;
  inspRenameAckBtn.disabled = true;
  inspRenameError.style.display = "none";
  inspRenameInput.classList.remove("collision");

  try {
    const r = await fetch(
      ENTITIES_BASE + "/" + encodeURIComponent(entityId) + "/surrogate",
      {
        method: "PATCH",
        headers: { "content-type": "application/json", "x-blindfold-workspace": currentWorkspace },
        body: JSON.stringify({ workspace: currentWorkspace, new_surrogate: newSurrogate }),
      }
    );
    if (r.status === 409) {
      // Collision: hard reject — red field error, rename blocked
      const body = await r.json();
      inspRenameInput.classList.add("collision");
      inspRenameError.textContent = "Surrogate collision: " + (body.detail || "already in use");
      inspRenameError.style.display = "";
      return;
    }
    if (r.status === 403) {
      inspRenameError.textContent = "Access denied — admin role required.";
      inspRenameError.style.display = "";
      return;
    }
    if (!r.ok) {
      inspRenameError.textContent = "Error " + r.status;
      inspRenameError.style.display = "";
      return;
    }
    const body = await r.json();
    if (body.inconsistent_dependents && body.inconsistent_dependents.length > 0 && !acknowledged) {
      // Soft banner: show dependent warning, require acknowledge
      inspRenameWarning.style.display = "";
      return;
    }
    // Success: update graph node label
    if (selectedNode) {
      selectedNode.data("label", newSurrogate);
      inspSurrogate.textContent = newSurrogate;
    }
    inspRenameInput.value = "";
    inspRenameWarning.style.display = "none";
  } finally {
    inspRenameBtn.disabled = false;
    if (!inspRenameAckBtn.disabled) inspRenameAckBtn.disabled = !inspAckCheckbox.checked;
  }
}

inspRenameBtn.addEventListener("click", () => submitRename(false));
inspRenameAckBtn.addEventListener("click", () => submitRename(true));
inspAckCheckbox.addEventListener("change", () => {
  inspRenameAckBtn.disabled = !inspAckCheckbox.checked;
});
inspCloseBtn.addEventListener("click", hideInspector);

// ---------------------------------------------------------------------------
// Per-node reveal badge (ADR-0015 / ADR-0017 / issue #30)
// ---------------------------------------------------------------------------

function doReveal(surrogate, onSuccess) {
  revealTarget = { surrogate, onSuccess };
  openDialog(revealAuditBackdrop);
}

revealConfirmYes.addEventListener("click", async () => {
  closeDialog(revealAuditBackdrop);
  if (!revealTarget) return;
  const { surrogate, onSuccess } = revealTarget;
  revealTarget = null;
  try {
    const r = await fetch(
      REIDENTIFY_BASE + "/" + encodeURIComponent(surrogate) + "/real",
      { headers: { "x-blindfold-workspace": currentWorkspace } }
    );
    if (r.status === 403) {
      // Mark badge as locked
      revealBadgeBtn.style.display = "none";
      revealBadgeLocked.style.display = "";
      if (onSuccess) onSuccess(null, "Access denied — re-identifier role required.");
      return;
    }
    if (!r.ok) {
      if (onSuccess) onSuccess(null, "Error " + r.status);
      return;
    }
    const body = await r.json();
    if (onSuccess) onSuccess(body.real, null);
  } catch (e) {
    if (onSuccess) onSuccess(null, String(e));
  }
});

revealConfirmNo.addEventListener("click", () => {
  closeDialog(revealAuditBackdrop);
  revealTarget = null;
});

revealBadgeBtn.addEventListener("click", () => {
  if (!selectedNode) return;
  const surrogate = selectedNode.data("label");
  doReveal(surrogate, (real, err) => {
    if (err) {
      revealBadgeLocked.textContent = REVEAL_LOCKED_LABEL;
      revealBadgeLocked.style.display = "";
      revealBadgeBtn.style.display = "none";
    } else {
      revealBadgeBtn.textContent = real;
    }
  });
});

// ---------------------------------------------------------------------------
// Drag-to-merge (issue #30)
// ---------------------------------------------------------------------------

function showMergeDialog(winnerNode, loserNode) {
  mergeWinner = { id: winnerNode.data("id"), label: winnerNode.data("label"), kind: winnerNode.data("kind") };
  mergeLoser  = { id: loserNode.data("id"),  label: loserNode.data("label"),  kind: loserNode.data("kind") };
  renderMergeCandidates();
  mergeError.style.display = "none";
  openDialog(mergeDlgBackdrop);
}

function renderMergeCandidates() {
  mergeWinnerSur.textContent  = mergeWinner.label;
  mergeWinnerKind.textContent = "Kind: " + mergeWinner.kind;
  mergeLoserSur.textContent   = mergeLoser.label;
  mergeLoserKind.textContent  = "Kind: " + mergeLoser.kind;
  // Reset reveal state
  [mergeWinnerRevealResult, mergeLoserRevealResult].forEach(el => { el.style.display = "none"; el.textContent = ""; });
  [mergeWinnerRevealError, mergeLoserRevealError].forEach(el => { el.style.display = "none"; el.textContent = ""; });
}

mergeSwapBtn.addEventListener("click", () => {
  const tmp = mergeWinner;
  mergeWinner = mergeLoser;
  mergeLoser = tmp;
  renderMergeCandidates();
});

function makeRevealHandler(surEl, resultEl, errEl) {
  return () => {
    doReveal(surEl.textContent.trim(), (real, err) => {
      if (err) {
        errEl.textContent = err;
        errEl.style.display = "";
      } else {
        resultEl.textContent = "Real: " + real;
        resultEl.style.display = "";
      }
    });
  };
}

mergeWinnerReveal.addEventListener("click", makeRevealHandler(mergeWinnerSur, mergeWinnerRevealResult, mergeWinnerRevealError));
mergeLoserReveal.addEventListener("click", makeRevealHandler(mergeLoserSur, mergeLoserRevealResult, mergeLoserRevealError));

mergeConfirmBtn.addEventListener("click", async () => {
  mergeConfirmBtn.disabled = true;
  mergeError.style.display = "none";
  try {
    const r = await fetch(MERGE_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        workspace: currentWorkspace,
        winner: { entity_id: mergeWinner.id },
        loser:  { entity_id: mergeLoser.id  },
      }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      mergeError.textContent = body.detail || ("Error " + r.status);
      mergeError.style.display = "";
      return;
    }
    // Collapse canvas: remove loser node, update winner label if needed
    const loserElem = cy.getElementById(mergeLoser.id);
    if (loserElem.length) loserElem.remove();
    closeDialog(mergeDlgBackdrop);
    mergeWinner = null;
    mergeLoser  = null;
    hideInspector();
  } finally {
    mergeConfirmBtn.disabled = false;
  }
});

mergeCancelBtn.addEventListener("click", () => {
  closeDialog(mergeDlgBackdrop);
});

// ---------------------------------------------------------------------------
// Edge draw (click-based draw mode, issues #27 / #30)
// ---------------------------------------------------------------------------

inspDrawEdgeBtn.addEventListener("click", () => {
  edgeDrawMode = !edgeDrawMode;
  edgePendingSource = edgeDrawMode ? selectedNode : null;
  inspDrawEdgeBtn.textContent = edgeDrawMode ? "Cancel draw" : "Draw edge";
  if (edgeDrawMode && selectedNode) {
    selectedNode.addClass("eh-source");
  } else {
    cy.nodes().removeClass("eh-source");
  }
});

// ---------------------------------------------------------------------------
// Edge delete (selected edge, issues #27 / #30)
// ---------------------------------------------------------------------------

inspDeleteEdgeBtn.addEventListener("click", async () => {
  if (!selectedEdge) return;
  const edgeId = selectedEdge.data("edgeId");
  if (!edgeId) return;
  inspDeleteEdgeBtn.disabled = true;
  try {
    const r = await fetch(
      GRAPH_BASE + "/" + encodeURIComponent(currentWorkspace) + "/" + RELATIONSHIPS_PATH + "/" + encodeURIComponent(edgeId),
      { method: "DELETE" }
    );
    if (r.ok) {
      selectedEdge.remove();
      selectedEdge = null;
      inspDeleteEdgeBtn.style.display = "none";
    } else {
      showGraphError("Failed to delete edge: " + r.status);
    }
  } finally {
    inspDeleteEdgeBtn.disabled = false;
  }
});

edgeCancelBtn.addEventListener("click", () => {
  closeDialog(edgePickerBackdrop);
  edgePendingSource = null;
  edgePendingTarget = null;
});

edgeConfirmBtn.addEventListener("click", async () => {
  const sourceNode = edgePendingSource;
  const targetNode = edgePendingTarget;
  if (!sourceNode || !targetNode) return;

  const sourceKind = sourceNode.data("kind");
  const targetKind = targetNode.data("kind");
  let relation = edgeTypeSelect.value;

  // Kind-aware vocabulary: auto-orient reverse-direction drag (term->person -> employer person->term)
  // person->term: employer only
  // term->term: subsidiary_of only
  // other combos: reject
  edgePickerError.style.display = "none";
  let orientedSource = sourceNode;
  let orientedTarget = targetNode;
  if (sourceKind === "person" && targetKind === "term") {
    relation = "employer";
  } else if (sourceKind === "term" && targetKind === "term") {
    relation = "subsidiary_of";
  } else if (sourceKind === "term" && targetKind === "person") {
    // Auto-orient: person->term employer
    orientedSource = targetNode;
    orientedTarget = sourceNode;
    relation = "employer";
  } else {
    edgePickerError.textContent = "Invalid kind pair — only person→term (employer) or term→term (subsidiary_of) are supported.";
    edgePickerError.style.display = "";
    return;
  }

  edgeConfirmBtn.disabled = true;
  try {
    const r = await fetch(
      GRAPH_BASE + "/" + encodeURIComponent(currentWorkspace) + "/" + RELATIONSHIPS_PATH,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_kind: orientedSource.data("kind"),
          source_id:   orientedSource.data("id"),
          relation,
          target_kind: orientedTarget.data("kind"),
          target_id:   orientedTarget.data("id"),
        }),
      }
    );
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      edgePickerError.textContent = body.detail || ("Error " + r.status);
      edgePickerError.style.display = "";
      return;
    }
    const created = await r.json();
    cy.add({
      group: "edges",
      data: {
        id: "edge-" + created.id,
        edgeId: created.id,
        source: orientedSource.data("id"),
        target: orientedTarget.data("id"),
        relation,
      },
    });
    closeDialog(edgePickerBackdrop);
    edgePendingSource = null;
    edgePendingTarget = null;
  } finally {
    edgeConfirmBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Cytoscape setup
// ---------------------------------------------------------------------------

cy = cytoscape({
  container: document.getElementById("cy"),
  style: [
    {
      selector: "node[kind='person']",
      style: {
        "background-color": "#4a90d9", label: "data(label)",
        "font-size": 12, color: "#fff",
        "text-valign": "center", "text-halign": "center",
        shape: "ellipse", width: 80, height: 40,
        "text-wrap": "wrap", "text-max-width": 70,
      },
    },
    {
      selector: "node[kind='term']",
      style: {
        "background-color": "#8b5cf6", label: "data(label)",
        "font-size": 12, color: "#fff",
        "text-valign": "center", "text-halign": "center",
        shape: "roundrectangle", width: 90, height: 40,
        "text-wrap": "wrap", "text-max-width": 80,
      },
    },
    { selector: "node:selected", style: { "border-width": 3, "border-color": "#f59e0b" } },
    { selector: "node.eh-source", style: { "border-width": 3, "border-color": "#22c55e" } },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier", "target-arrow-shape": "triangle",
        label: "data(relation)", "font-size": 10, "text-rotation": "autorotate",
        "line-color": "#aaa", "target-arrow-color": "#aaa",
      },
    },
    { selector: "edge:selected", style: { "line-color": "#f59e0b", "target-arrow-color": "#f59e0b" } },
  ],
  layout: { name: "cose", animate: false },
  elements: [],
});

// Node select: show inspector + reveal badge
cy.on("select", "node", evt => {
  showInspector(evt.target.data(), evt.target);
  selectedEdge = null;
  inspDeleteEdgeBtn.style.display = "none";
});

cy.on("unselect", "node", () => {
  if (!edgeDrawMode) hideInspector();
});

// Edge select: show delete button in inspector
cy.on("select", "edge", evt => {
  selectedEdge = evt.target;
  inspDeleteEdgeBtn.style.display = "";
  inspector.style.display = "";
});

cy.on("unselect", "edge", () => {
  selectedEdge = null;
  inspDeleteEdgeBtn.style.display = "none";
});

// Drag-to-merge: detect node released on top of another node
cy.on("freeon", "node", evt => {
  const released = evt.target;
  const pos = released.position();
  const overlap = cy.nodes().not(released).filter(n => {
    const bb = n.boundingBox();
    return pos.x >= bb.x1 && pos.x <= bb.x2 && pos.y >= bb.y1 && pos.y <= bb.y2;
  });
  if (overlap.length === 0) return;
  const target = overlap.first();
  if (released.data("kind") !== target.data("kind")) {
    showGraphError("Cross-kind merge is not supported (person↔person or term↔term only).");
    return;
  }
  // Dragged node = default winner; drop target = loser (confirmed in dialog)
  showMergeDialog(released, target);
});

// Edge draw mode: tap on target node to complete the edge
cy.on("tap", "node", evt => {
  if (!edgeDrawMode || !edgePendingSource) return;
  const target = evt.target;
  if (target.id() === edgePendingSource.id()) return;
  edgeDrawMode = false;
  inspDrawEdgeBtn.textContent = "Draw edge";
  cy.nodes().removeClass("eh-source");

  edgePendingTarget = target;
  const srcLabel = edgePendingSource.data("label");
  const tgtLabel = target.data("label");
  edgeDirectionLabel.textContent = srcLabel + " → " + tgtLabel;

  // Pre-select the valid relation for this kind pair
  const sk = edgePendingSource.data("kind");
  const tk = target.data("kind");
  if (sk === "term" && tk === "term") {
    edgeTypeSelect.value = "subsidiary_of";
  } else {
    edgeTypeSelect.value = "employer";
  }
  edgePickerError.style.display = "none";
  openDialog(edgePickerBackdrop);
});

// ---------------------------------------------------------------------------
// Workspace selector + graph loader
// ---------------------------------------------------------------------------

function ensureWorkspaceOption(slug) {
  if (![...wsSelect.options].some(o => o.value === slug)) {
    const opt = document.createElement("option");
    opt.value = opt.textContent = slug;
    wsSelect.appendChild(opt);
  }
  wsSelect.value = slug;
  currentWorkspace = slug;
}

async function loadGraph(workspace) {
  hideGraphError();
  hideInspector();
  try {
    const r = await fetch(GRAPH_BASE + "/" + encodeURIComponent(workspace) + "/graph");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const { nodes, edges } = await r.json();
    cy.elements().remove();
    cy.add([
      ...nodes.map(n => ({
        group: "nodes",
        data: { id: n.id, label: n.label, kind: n.kind },
      })),
      ...edges.map(e => ({
        group: "edges",
        data: {
          id: "edge-" + e.id,
          edgeId: e.id,
          source: e.source,
          target: e.target,
          relation: e.relation,
        },
      })),
    ]);
    cy.layout({ name: "cose", animate: false }).run();
  } catch (e) {
    showGraphError(String(e));
  }
}

const params = new URLSearchParams(location.search);
const initWs = params.get("workspace") || "default";
ensureWorkspaceOption(initWs);
wsSelect.addEventListener("change", () => {
  ensureWorkspaceOption(wsSelect.value);
  loadGraph(currentWorkspace);
});
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
  td.cb-col { width: 2rem; text-align: center; }
  th.cb-col { width: 2rem; text-align: center; cursor: default; }
  th.cb-col:hover { background: none; }
  tr.highlighted td { background: #fffbe6; }
  tr.cb-disabled { opacity: 0.4; pointer-events: none; }
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
  /* Merge action bar */
  #merge-bar { display: none; align-items: center; gap: 0.75rem; background: #f0f7ff; border: 1px solid #b0d0f0; border-radius: 4px; padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; }
  #merge-bar.visible { display: flex; }
  button.merge-btn { background: #1f5fa6; color: white; border: none; padding: 0.3rem 0.8rem; border-radius: 3px; cursor: pointer; font-size: 0.9rem; font-weight: 600; }
  button.merge-btn[disabled] { opacity: 0.6; cursor: progress; }
  #merge-bar-msg { font-size: 0.9rem; color: #333; }
  /* Merge dialog overlay */
  #merge-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45);
    z-index: 100; align-items: center; justify-content: center;
  }
  #merge-overlay.visible { display: flex; }
  #merge-dialog {
    background: #fff; border-radius: 6px; padding: 1.5rem; max-width: 640px; width: 90%;
    box-shadow: 0 4px 16px rgba(0,0,0,.25);
  }
  #merge-dialog h2 { font-size: 1.1rem; margin: 0 0 1rem; }
  .merge-candidates { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0.75rem; align-items: start; margin-bottom: 1rem; }
  .merge-card { border: 1px solid #ddd; border-radius: 4px; padding: 0.75rem; }
  .merge-card h3 { font-size: 0.85rem; font-weight: 700; margin: 0 0 0.5rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .merge-card h3.survivor { color: #1f7a3f; }
  .merge-card h3.retired-label { color: #b00020; }
  .merge-card .surrogate { font-family: ui-monospace, monospace; font-size: 0.9rem; margin-bottom: 0.25rem; }
  .merge-card .variations { font-size: 0.82rem; color: #555; margin-bottom: 0.4rem; }
  .merge-swap-col { display: flex; align-items: center; justify-content: center; }
  button.swap-btn { background: #f5f5f5; border: 1px solid #ccc; border-radius: 3px; padding: 0.3rem 0.6rem; cursor: pointer; font-size: 0.9rem; }
  button.swap-btn:hover { background: #e8e8e8; }
  .merge-dialog-footer { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1rem; }
  button.merge-confirm-btn { background: #1f7a3f; color: white; border: none; padding: 0.4rem 1rem; border-radius: 3px; cursor: pointer; font-weight: 600; }
  button.merge-confirm-btn[disabled] { opacity: 0.6; cursor: progress; }
  button.merge-cancel-btn { background: #f5f5f5; border: 1px solid #ccc; padding: 0.4rem 1rem; border-radius: 3px; cursor: pointer; }
  .merge-error { color: #b00020; font-size: 0.85rem; margin-top: 0.5rem; }
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
    <div id="merge-bar">
      <span id="merge-bar-msg"></span>
      <button class="merge-btn" id="merge-btn">Merge…</button>
    </div>
    <div class="error" id="list-error" style="display:none"></div>
    <div class="ceiling-msg" id="ceiling-msg" style="display:none">
      More than 150 entities — narrow with surrogate filters or use real-name search to find specific records.
    </div>
    <p class="empty" id="loading-msg">Loading…</p>
    <table id="entity-table" style="display:none">
      <thead>
        <tr>
          <th class="cb-col"></th>
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

  <!-- Merge winner/loser confirm dialog -->
  <div id="merge-overlay" role="dialog" aria-modal="true" aria-labelledby="merge-dialog-title">
    <div id="merge-dialog">
      <h2 id="merge-dialog-title">Confirm merge</h2>
      <div class="merge-candidates">
        <div class="merge-card" id="winner-card">
          <h3 class="survivor">Survivor</h3>
          <div class="surrogate" id="winner-surrogate"></div>
          <div class="variations" id="winner-variations"></div>
          <div id="winner-reveal-area"></div>
        </div>
        <div class="merge-swap-col">
          <button class="swap-btn" id="swap-btn" title="Swap survivor and retired">⇄ Swap</button>
        </div>
        <div class="merge-card" id="loser-card">
          <h3 class="retired-label">Retired</h3>
          <div class="surrogate" id="loser-surrogate"></div>
          <div class="variations" id="loser-variations"></div>
          <div id="loser-reveal-area"></div>
        </div>
      </div>
      <div class="merge-error" id="merge-dialog-error" style="display:none"></div>
      <div class="merge-dialog-footer">
        <button class="merge-cancel-btn" id="merge-cancel-btn">Cancel</button>
        <button class="merge-confirm-btn" id="merge-confirm-btn">Confirm merge</button>
      </div>
    </div>
  </div>

<script type="module">
// Endpoint base paths (ADR-0011 / issue #32 / issue #33 / issue #34).
// /v1/management/workspaces/<slug>/entities          — surrogate-space entity list
// /v1/management/workspaces/<slug>/entities/search   — real-name search (re-identifier role)
// /v1/management/workspaces/<slug>/relationships     — edge CRUD (no role required, #27)
// /v1/management/workspaces/<slug>/entities/merge    — merge by entity_id (admin role, ADR-0016)
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
const mergeBar        = document.getElementById("merge-bar");
const mergeBarMsg     = document.getElementById("merge-bar-msg");
const mergeBtn        = document.getElementById("merge-btn");
const mergeOverlay    = document.getElementById("merge-overlay");
const winnerSurrogate = document.getElementById("winner-surrogate");
const winnerVariations= document.getElementById("winner-variations");
const winnerRevealArea= document.getElementById("winner-reveal-area");
const loserSurrogate  = document.getElementById("loser-surrogate");
const loserVariations = document.getElementById("loser-variations");
const loserRevealArea = document.getElementById("loser-reveal-area");
const swapBtn         = document.getElementById("swap-btn");
const mergeDialogError= document.getElementById("merge-dialog-error");
const mergeCancelBtn  = document.getElementById("merge-cancel-btn");
const mergeConfirmBtn = document.getElementById("merge-confirm-btn");

let allRows    = [];
let highlighted = new Set();
let sortCol    = "active_surrogate";
let sortAsc    = true;
let canSearch  = false; // true when caller holds re-identifier role

// Checkbox pair-select state (issue #34).
let checked = []; // array of 0, 1, or 2 entity_ids

// Dialog state: winner/loser designations (entity_id), swappable before confirm.
let dialogWinnerId = null;
let dialogLoserId  = null;

// Detect re-identifier capability by attempting a search on a known-empty string.
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
  checked = [];
  updateMergeBar();
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
// Checkbox pair-select (issue #34)
// ---------------------------------------------------------------------------

// The kind of the first checked entity gates which other rows are selectable.
function checkedKind() {
  if (checked.length === 0) return null;
  const row = allRows.find(r => r.entity_id === checked[0]);
  return row ? row.kind : null;
}

function updateMergeBar() {
  if (checked.length === 2) {
    const ids = checked.map(id => allRows.find(r => r.entity_id === id));
    const surrogates = ids.map(r => r ? r.active_surrogate : "?");
    mergeBarMsg.textContent = `2 entities selected: ${surrogates.join(" + ")}`;
    mergeBar.classList.add("visible");
  } else {
    mergeBar.classList.remove("visible");
  }
}

function handleCheckbox(row, cb) {
  if (cb.checked) {
    if (checked.length >= 2) { cb.checked = false; return; }
    checked.push(row.entity_id);
  } else {
    checked = checked.filter(id => id !== row.entity_id);
  }
  updateMergeBar();
  // Re-render to apply kind-gating on the remaining checkboxes.
  renderTable();
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

function renderTable() {
  const rows = sortedAndFiltered();
  entityTbody.innerHTML = "";
  const gatingKind = checkedKind();
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (highlighted.has(row.entity_id)) tr.classList.add("highlighted");

    // Checkbox cell (issue #34).
    const cbTd = document.createElement("td");
    cbTd.className = "cb-col";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = checked.includes(row.entity_id);
    // Kind-gate: once a row is checked, disable non-same-kind rows and self-row
    // if two are already checked and this one isn't among them.
    const kindBlocked = gatingKind !== null && row.kind !== gatingKind;
    const capBlocked  = checked.length >= 2 && !checked.includes(row.entity_id);
    cb.disabled = kindBlocked || capBlocked;
    if (kindBlocked || capBlocked) { tr.classList.add("cb-disabled"); }
    cb.addEventListener("change", () => handleCheckbox(row, cb));
    cbTd.appendChild(cb);
    tr.appendChild(cbTd);

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

// Build a reveal badge for the merge dialog (gated by re-identifier role).
// The badge calls the re-identify endpoint (ADR-0015) and shows the real value
// inline — every dialog reveal is audited exactly like the table-row reveal.
function buildDialogRevealBadge(surrogate) {
  const area = document.createElement("div");
  if (!canSearch) {
    const lock = document.createElement("span");
    lock.className = "reveal-badge locked";
    lock.textContent = "🔒 Reveal locked";
    lock.title = "re-identifier role required";
    area.appendChild(lock);
    return area;
  }
  const btn = document.createElement("button");
  btn.className = "reveal-badge";
  btn.textContent = "Reveal";
  btn.title = "This will be logged";
  btn.addEventListener("click", async () => {
    if (!confirm("Revealing the real value will be logged. Continue?")) return;
    btn.disabled = true;
    const workspace = wsSelect.value;
    try {
      const r = await fetch(
        `${REIDENTIFY_BASE}/${encodeURIComponent(surrogate)}/real`,
        { headers: { "x-blindfold-workspace": workspace } }
      );
      if (r.status === 403) { btn.disabled = false; return; }
      if (!r.ok) { btn.disabled = false; return; }
      const body = await r.json();
      const val = document.createElement("span");
      val.className = "reveal-value";
      val.textContent = body.real;
      btn.replaceWith(val);
    } catch (_) {
      btn.disabled = false;
    }
  });
  area.appendChild(btn);
  return area;
}

function populateDialogCard(
  surrogateEl, variationsEl, revealAreaEl, entityId
) {
  const row = allRows.find(r => r.entity_id === entityId);
  if (!row) return;
  surrogateEl.textContent = row.active_surrogate;
  const vars = row.retired_surrogates || [];
  variationsEl.textContent = vars.length
    ? `Also known as: ${vars.join(", ")}`
    : "";
  revealAreaEl.innerHTML = "";
  revealAreaEl.appendChild(buildDialogRevealBadge(row.active_surrogate));
}

function openMergeDialog() {
  if (checked.length !== 2) return;
  // Default: first checked = winner, second = loser.
  // Check order does NOT determine winner/loser — the dialog's Swap and the
  // Confirm button are the sole authority (issue #34).
  dialogWinnerId = checked[0];
  dialogLoserId  = checked[1];
  refreshDialogCards();
  mergeDialogError.style.display = "none";
  mergeConfirmBtn.disabled = false;
  mergeOverlay.classList.add("visible");
}

function refreshDialogCards() {
  populateDialogCard(winnerSurrogate, winnerVariations, winnerRevealArea, dialogWinnerId);
  populateDialogCard(loserSurrogate,  loserVariations,  loserRevealArea,  dialogLoserId);
}

function closeMergeDialog() {
  mergeOverlay.classList.remove("visible");
  dialogWinnerId = null;
  dialogLoserId  = null;
}

swapBtn.addEventListener("click", () => {
  [dialogWinnerId, dialogLoserId] = [dialogLoserId, dialogWinnerId];
  refreshDialogCards();
});

mergeCancelBtn.addEventListener("click", closeMergeDialog);

mergeConfirmBtn.addEventListener("click", async () => {
  if (!dialogWinnerId || !dialogLoserId) return;
  mergeConfirmBtn.disabled = true;
  mergeDialogError.style.display = "none";
  const workspace = wsSelect.value;
  try {
    const r = await fetch(
      `${ENTITIES_BASE}/${encodeURIComponent(workspace)}/entities/merge`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "x-blindfold-identity": "" },
        body: JSON.stringify({ winner_id: dialogWinnerId, loser_id: dialogLoserId }),
      }
    );
    if (!r.ok) {
      const detail = await r.json().then(b => b.detail || `HTTP ${r.status}`).catch(() => `HTTP ${r.status}`);
      mergeDialogError.textContent = `Merge failed: ${detail}`;
      mergeDialogError.style.display = "";
      mergeConfirmBtn.disabled = false;
      return;
    }
    // Collapse: remove loser row from allRows; clear selection.
    allRows = allRows.filter(row => row.entity_id !== dialogLoserId);
    checked = [];
    closeMergeDialog();
    updateMergeBar();
    renderTable();
  } catch (e) {
    mergeDialogError.textContent = String(e);
    mergeDialogError.style.display = "";
    mergeConfirmBtn.disabled = false;
  }
});

mergeBtn.addEventListener("click", openMergeDialog);

// Close dialog on backdrop click.
mergeOverlay.addEventListener("click", e => {
  if (e.target === mergeOverlay) closeMergeDialog();
});

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

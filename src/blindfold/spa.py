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
REIDENTIFY_ENDPOINT = "/v1/management/surrogate"
MERGE_ENDPOINT = "/v1/management/entities/merge"
EDIT_SURROGATE_ENDPOINT = "/v1/management/entities"


def review_inbox_html() -> str:
    """Return the SPA bundle as a self-contained HTML page."""
    return _HTML


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

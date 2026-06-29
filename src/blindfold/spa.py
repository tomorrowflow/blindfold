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


def review_inbox_html() -> str:
    """Return the SPA bundle as a self-contained HTML page."""
    return _HTML


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

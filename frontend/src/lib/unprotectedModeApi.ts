// Settings -> Unprotected mode capability toggle (issue #188, ADR-0038). Backs
// onto #180's proxy-side capability flag: GET /v1/status's unprotected_mode
// object is the only read surface (the capability endpoint itself is write-only),
// POST /v1/unprotected-mode/capability flips it. Deliberately unauthenticated,
// like the rest of /v1/status (ADR-0011/0019) -- the security boundary is the
// loopback-only bind, not per-call auth.

export async function fetchUnprotectedModeCapability(): Promise<boolean> {
  const r = await fetch("/v1/status");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = await r.json();
  return Boolean(body.unprotected_mode?.capability_enabled);
}

// Payload inspection's Unprotected-mode-active empty state (issue #432, ADR-0059
// §4's "nothing is retained while Unprotected mode is active"): read the same
// `/v1/status` field, this time the *active* override rather than the
// capability that merely permits invoking it.
export async function fetchUnprotectedModeActive(): Promise<boolean> {
  const r = await fetch("/v1/status");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = await r.json();
  return Boolean(body.unprotected_mode?.active);
}

export async function setUnprotectedModeCapability(enabled: boolean): Promise<boolean> {
  const r = await fetch("/v1/unprotected-mode/capability", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = await r.json();
  return Boolean(body.capability_enabled);
}

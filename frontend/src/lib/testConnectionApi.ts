// Shared fetch seam for POST /v1/management/test-connection (viewer-gated, issue
// #265): drives one real exchange through the proxy's own listening socket and
// returns a typed verdict -- never a bare "failed" string. Mirrors auditApi.ts's
// {locked: true} | {locked: false, ...} discriminated-union shape.

export type TestConnectionCode =
  | "blindfolded_ok"
  | "blindfolded_ok_restore_unproven"
  | "proxy_unreachable"
  | "wrong_endpoint"
  | "upstream_auth_rejected"
  | "upstream_unreachable"
  | "fail_closed_block"
  | "leak_flagged";

export type TestConnectionVerdict = {
  code: TestConnectionCode;
  message: string;
  remedy: string;
  ref: string | null;
};

export type TestConnectionResult =
  | { locked: true }
  | { locked: false; verdict: TestConnectionVerdict };

export async function runTestConnection(
  workspace: string,
  baseUrl: string,
  model: string,
  credential: string
): Promise<TestConnectionResult> {
  const headers: Record<string, string> = {};
  if (credential.trim()) {
    headers["x-api-key"] = credential.trim();
  }
  const resp = await fetch("/v1/management/test-connection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace, base_url: baseUrl, model, headers }),
  });
  if (resp.status === 403) {
    return { locked: true };
  }
  const verdict = (await resp.json()) as TestConnectionVerdict;
  return { locked: false, verdict };
}

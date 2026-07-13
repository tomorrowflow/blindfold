import { test, expect, WORKSPACE } from "./fixtures";
import { request as pwRequest } from "@playwright/test";

// Settings -> Import (issue #116): bulk-seed the entity graph from CSV/JSON with a
// preview-before-commit step, closing a design-fidelity gap (brief §3.7,
// ADR-0013's seed-first model). Frontend-only -- the backend seam already exists
// (POST /v1/management/workspaces/{slug}/seed, issue #108/#109).
//
// Fixture roles (serve_fixture.py): alice holds admin (+ viewer/curator/re-identifier)
// on WORKSPACE ("acme").

test.describe("settings import — section renders", () => {
  test("Import section renders under Preferences with a dropzone", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");
    await expect(alicePage.getByRole("heading", { name: "Import" })).toBeVisible();
    await expect(alicePage.locator("body")).toContainText("Bulk seed the entity graph");
    await expect(alicePage.getByTestId("import-dropzone")).toBeVisible();
  });
});

test.describe("settings import — JSON preview", () => {
  test("selecting a JSON bundle previews rows without committing", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");

    const bundle = {
      persons: [{ canonical_name: "Priya Sharma", variations: ["Priya"] }],
      terms: [{ canonical_name: "Zentek Solutions", variations: [] }],
      entity_relationships: [
        {
          source_kind: "person",
          source: "Priya Sharma",
          relation: "employer",
          target_kind: "term",
          target: "Zentek Solutions",
        },
      ],
    };

    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });

    const table = alicePage.getByTestId("import-preview-table");
    await expect(table).toBeVisible();
    await expect(table).toContainText("Priya Sharma");
    await expect(table).toContainText("Zentek Solutions");
    await expect(table).toContainText("employer");

    // Preview-before-commit: no seed request fired yet.
    let seedRequestFired = false;
    alicePage.on("request", (req) => {
      if (req.url().includes("/seed")) seedRequestFired = true;
    });
    await alicePage.waitForTimeout(200);
    expect(seedRequestFired).toBe(false);
  });
});

test.describe("settings import — preview dual-encoding", () => {
  test("Kind is dual-encoded (color mark + text label) and Relationship is mono", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");

    const bundle = {
      persons: [{ canonical_name: "Priya Sharma", variations: [] }],
      terms: [{ canonical_name: "Zentek Solutions", variations: [] }],
      entity_relationships: [
        {
          source_kind: "person",
          source: "Priya Sharma",
          relation: "employer",
          target_kind: "term",
          target: "Zentek Solutions",
        },
      ],
    };

    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });

    const table = alicePage.getByTestId("import-preview-table");
    const rows = table.locator("tbody tr");
    await expect(
      rows.filter({ hasText: "Priya Sharma" }).first().locator(".bf-kind-mark--person")
    ).toHaveCount(1);
    await expect(
      rows.filter({ hasText: "Zentek Solutions" }).first().locator(".bf-kind-mark--term")
    ).toHaveCount(1);
    await expect(table.locator(".bf-mono-cell", { hasText: "employer" })).toBeVisible();
  });
});

test.describe("settings import — drag and drop", () => {
  test("dragging a JSON file onto the dropzone previews it without clicking to browse", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");

    const bundle = {
      persons: [{ canonical_name: "Léa Fontaine", variations: [] }],
      terms: [],
      entity_relationships: [],
    };

    const dataTransfer = await alicePage.evaluateHandle(
      ({ contents, name, type }) => {
        const dt = new DataTransfer();
        const file = new File([contents], name, { type });
        dt.items.add(file);
        return dt;
      },
      { contents: JSON.stringify(bundle), name: "bundle.json", type: "application/json" }
    );
    await alicePage.dispatchEvent("[data-testid='import-dropzone']", "drop", { dataTransfer });

    const table = alicePage.getByTestId("import-preview-table");
    await expect(table).toBeVisible();
    await expect(table).toContainText("Léa Fontaine");
  });
});

test.describe("settings import — CSV preview", () => {
  test("selecting a CSV file previews the same row shape as JSON", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");

    const csv = [
      "kind,value,variations,relation,target",
      "person,Priya Sharma,Priya,employer,Zentek Solutions",
      "term,Zentek Solutions,,,",
    ].join("\n");

    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(csv),
    });

    const table = alicePage.getByTestId("import-preview-table");
    await expect(table).toBeVisible();
    await expect(table).toContainText("Priya Sharma");
    await expect(table).toContainText("Zentek Solutions");
    await expect(table).toContainText("employer");
  });
});

test.describe("settings import — commit / discard", () => {
  test("Commit posts the parsed bundle to the seed endpoint for the active workspace", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");

    const bundle = {
      persons: [{ canonical_name: "Priya Sharma", variations: ["Priya"] }],
      terms: [],
      entity_relationships: [],
    };

    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });
    await expect(alicePage.getByTestId("import-preview-table")).toBeVisible();

    const [seedRequest] = await Promise.all([
      alicePage.waitForRequest((req) => req.url().includes("/seed") && req.method() === "POST"),
      alicePage.getByTestId("import-commit-btn").click(),
    ]);
    expect(seedRequest.url()).toContain("/v1/management/workspaces/acme/seed");
    const postedBody = seedRequest.postDataJSON();
    expect(postedBody.bundle.persons[0].canonical_name).toBe("Priya Sharma");

    // Commit clears the preview once the seed request succeeds.
    await expect(alicePage.getByTestId("import-preview-table")).not.toBeVisible();
  });

  test("Discard clears the preview without any network call", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");

    const bundle = { persons: [{ canonical_name: "Priya Sharma", variations: [] }], terms: [], entity_relationships: [] };
    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });
    await expect(alicePage.getByTestId("import-preview-table")).toBeVisible();

    let seedRequestFired = false;
    alicePage.on("request", (req) => {
      if (req.url().includes("/seed")) seedRequestFired = true;
    });

    await alicePage.getByTestId("import-discard-btn").click();
    await expect(alicePage.getByTestId("import-preview-table")).not.toBeVisible();
    await alicePage.waitForTimeout(200);
    expect(seedRequestFired).toBe(false);
  });
});

test.describe("settings import — leak audit", () => {
  test("commit mints a surrogate server-side; the real value is never returned by the entities API", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/settings");

    const realName = "Nadia Kessler";
    const bundle = { persons: [{ canonical_name: realName, variations: [] }], terms: [], entity_relationships: [] };
    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });
    await alicePage.getByTestId("import-commit-btn").click();
    await expect(alicePage.getByTestId("import-preview-table")).not.toBeVisible();

    const api = await pwRequest.newContext({
      baseURL,
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const res = await api.get(`/v1/management/workspaces/${WORKSPACE}/entities`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    await api.dispose();

    const surrogates = (body.entities as Array<{ active_surrogate: string }>).map(
      (e) => e.active_surrogate
    );
    expect(surrogates).not.toContain(realName);
    expect(surrogates.length).toBeGreaterThan(0);
  });

  test("parsing, previewing and committing issue zero requests to a non-loopback origin", async ({
    alicePage,
    baseURL,
  }) => {
    const requestHosts = new Set<string>();
    alicePage.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await alicePage.goto("/ui/settings");
    const bundle = { persons: [{ canonical_name: "Omar Farah", variations: [] }], terms: [], entity_relationships: [] };
    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });
    await expect(alicePage.getByTestId("import-preview-table")).toBeVisible();
    await alicePage.getByTestId("import-commit-btn").click();
    await expect(alicePage.getByTestId("import-preview-table")).not.toBeVisible();

    const firstPartyHost = new URL(baseURL!).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
  });

  test("the inbound real value never lands in localStorage or sessionStorage", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const realName = "Yusuf Demir — leak-check-marker";
    const bundle = { persons: [{ canonical_name: realName, variations: [] }], terms: [], entity_relationships: [] };
    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(bundle)),
    });
    await expect(alicePage.getByTestId("import-preview-table")).toBeVisible();

    const storageBeforeCommit = await alicePage.evaluate(() => ({
      local: JSON.stringify(localStorage),
      session: JSON.stringify(sessionStorage),
    }));
    expect(storageBeforeCommit.local).not.toContain(realName);
    expect(storageBeforeCommit.session).not.toContain(realName);

    await alicePage.getByTestId("import-commit-btn").click();
    await expect(alicePage.getByTestId("import-preview-table")).not.toBeVisible();

    const storageAfterCommit = await alicePage.evaluate(() => ({
      local: JSON.stringify(localStorage),
      session: JSON.stringify(sessionStorage),
    }));
    expect(storageAfterCommit.local).not.toContain(realName);
    expect(storageAfterCommit.session).not.toContain(realName);
  });
});

test.describe("settings import — malformed input", () => {
  test("malformed JSON shows a parse error, not a crash, and nothing commits", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    await alicePage.getByTestId("import-file-input").setInputFiles({
      name: "bundle.json",
      mimeType: "application/json",
      buffer: Buffer.from("{not valid json"),
    });

    await expect(alicePage.getByTestId("import-parse-error")).toBeVisible();
    await expect(alicePage.getByTestId("import-preview-table")).not.toBeVisible();
    await expect(alicePage.locator("nav.bf-sidebar")).toBeVisible();
  });
});

test.describe("settings — no export note", () => {
  test("a closing note states there is no export", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");
    await expect(alicePage.getByTestId("settings-no-export-note")).toContainText(
      "No export. Colleague sharing goes through the shared surrogate store and workspace roles; the voice-diary consumes the JSON API."
    );
  });
});

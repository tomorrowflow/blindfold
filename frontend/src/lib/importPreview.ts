// Settings -> Import file parsing (issue #116, extended by #127). Parses an
// operator-supplied CSV or JSON file into the same seed-bundle shape the seed/
// seed-preview endpoints already consume (persons/terms: {canonical_name,
// variations}; entity_relationships: {source_kind, source, relation, target_kind,
// target} -- see src/blindfold/store/vendored_seed.json, ADR-0029), so both
// preview and commit reuse the one shared VendoredSeedRepository path, never a
// second bundle schema.
//
// Entirely client-side: nothing here makes a network call. Row-level validation
// (blind-index duplicates, unknown relation type, orientation) happens server-side
// against the live entity graph -- see setupApi.ts's previewSeedBundle().

export type Referent = { canonical_name: string; variations: string[] };
export type EntityRelationship = {
  source_kind: string;
  source: string;
  relation: string;
  target_kind: string;
  target: string;
};

export type SeedBundle = {
  persons: Referent[];
  terms: Referent[];
  entity_relationships: EntityRelationship[];
};

export function parseJsonBundle(text: string): SeedBundle {
  const parsed = JSON.parse(text);
  return {
    persons: parsed.persons ?? [],
    terms: parsed.terms ?? [],
    entity_relationships: parsed.entity_relationships ?? [],
  };
}

// CSV columns: kind,value,variations,relation,target. `variations` is a
// semicolon-separated list. `relation`/`target` are optional -- when present,
// the row ALSO declares an entity_relationships edge from `value` to `target`
// (controlled vocabulary, CONTEXT.md: employer, subsidiary_of).
export function parseCsvBundle(text: string): SeedBundle {
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  const [header, ...dataLines] = lines;
  const columns = header.split(",").map((c) => c.trim().toLowerCase());
  const col = (name: string) => columns.indexOf(name);
  const kindIdx = col("kind");
  const valueIdx = col("value");
  const variationsIdx = col("variations");
  const relationIdx = col("relation");
  const targetIdx = col("target");
  if (kindIdx === -1 || valueIdx === -1) {
    throw new Error("CSV must have kind and value columns");
  }

  const bundle: SeedBundle = { persons: [], terms: [], entity_relationships: [] };
  for (const line of dataLines) {
    const cells = line.split(",").map((c) => c.trim());
    const kind = cells[kindIdx];
    const value = cells[valueIdx];
    const variations = (variationsIdx === -1 ? "" : cells[variationsIdx] ?? "")
      .split(";")
      .map((v) => v.trim())
      .filter((v) => v.length > 0);
    const referent: Referent = { canonical_name: value, variations };
    if (kind === "term") {
      bundle.terms.push(referent);
    } else {
      bundle.persons.push(referent);
    }

    const relation = relationIdx === -1 ? "" : cells[relationIdx] ?? "";
    const target = targetIdx === -1 ? "" : cells[targetIdx] ?? "";
    if (relation && target) {
      bundle.entity_relationships.push({
        source_kind: kind === "term" ? "term" : "person",
        source: value,
        relation,
        target_kind: "term",
        target,
      });
    }
  }
  return bundle;
}

export function parseImportFile(text: string, filename: string): SeedBundle {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".json")) {
    return parseJsonBundle(text);
  }
  if (lower.endsWith(".csv")) {
    return parseCsvBundle(text);
  }
  throw new Error("Unsupported file type");
}

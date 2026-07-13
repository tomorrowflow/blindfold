// Entity list view (issue #97): migrated into the shell. Behavior authority is the
// settled entity-list design (docs/design/entity-list-view-design-brief.md + its
// Decision Memo, ADR-0016/0017/0018) and the shipped /ui/entity-list behavior;
// visual authority is the final design (tokens, shell layout, segmented search-mode
// toggle, entity-centric single table per brief §7 resolution).

import { useEffect, useMemo, useState } from "react";
import { useWorkspace } from "../components/WorkspaceContext";
import { usePreferences } from "../components/PreferencesContext";
import { EntityListRow } from "../components/EntityListRow";
import { EntityListEmptyState } from "../components/EntityListEmptyState";
import { MergeDialog } from "../components/MergeDialog";
import {
  ENTITY_LIST_CEILING,
  fetchEntities,
  searchByRealName,
  type EdgeSummary,
  type EntityListRow as Row,
} from "../lib/entityListApi";

type SearchMode = "surrogate" | "real-name";
type SortCol = "active_surrogate" | "kind" | "employer" | "retired_surrogates";

function edgeSortValue(row: Row, relation: "employer" | "subsidiary_of"): string {
  const edge = row.edges.find((e) => e.relation === relation && e.direction === "outbound");
  return edge ? edge.other_surrogate : "";
}

export function EntityList() {
  const { activeWorkspace } = useWorkspace();
  const { density } = usePreferences();
  const workspace = activeWorkspace?.slug ?? null;
  const canReveal = activeWorkspace?.roles.includes("re-identifier") ?? false;

  const [allRows, setAllRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overCeiling, setOverCeiling] = useState(false);

  const [kindFilter, setKindFilter] = useState<"" | "person" | "term">("");
  const [surrogateFilter, setSurrogateFilter] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("surrogate");
  const [realNameQuery, setRealNameQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());

  const [sortCol, setSortCol] = useState<SortCol>("active_surrogate");
  const [sortAsc, setSortAsc] = useState(true);

  const [mergePair, setMergePair] = useState<{ winner: Row; loser: Row } | null>(null);

  useEffect(() => {
    if (!workspace) {
      setAllRows([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setOverCeiling(false);
    setHighlighted(new Set());
    fetchEntities(workspace)
      .then((rows) => {
        if (cancelled) return;
        if (rows.length > ENTITY_LIST_CEILING) {
          setOverCeiling(true);
          setAllRows([]);
        } else {
          setAllRows(rows);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspace]);

  const visibleRows = useMemo(() => {
    let rows = allRows;
    if (kindFilter) rows = rows.filter((r) => r.kind === kindFilter);
    if (surrogateFilter.trim()) {
      const needle = surrogateFilter.trim().toLowerCase();
      rows = rows.filter((r) => r.active_surrogate.toLowerCase().includes(needle));
    }
    const sorted = [...rows].sort((a, b) => {
      let va = "";
      let vb = "";
      if (sortCol === "active_surrogate") {
        va = a.active_surrogate;
        vb = b.active_surrogate;
      } else if (sortCol === "kind") {
        va = a.kind;
        vb = b.kind;
      } else if (sortCol === "employer") {
        va = edgeSortValue(a, "employer");
        vb = edgeSortValue(b, "employer");
      } else if (sortCol === "retired_surrogates") {
        va = a.retired_surrogates.join(",");
        vb = b.retired_surrogates.join(",");
      }
      const cmp = va.localeCompare(vb);
      return sortAsc ? cmp : -cmp;
    });
    return sorted;
  }, [allRows, kindFilter, surrogateFilter, sortCol, sortAsc]);

  function toggleSort(col: SortCol) {
    if (sortCol === col) {
      setSortAsc((v) => !v);
    } else {
      setSortCol(col);
      setSortAsc(true);
    }
  }

  function handleRenamed(entityId: string, newSurrogate: string) {
    setAllRows((rows) =>
      rows.map((r) => (r.entity_id === entityId ? { ...r, active_surrogate: newSurrogate } : r))
    );
  }

  function handleEdgesChanged(entityId: string, edges: EdgeSummary[]) {
    setAllRows((rows) => rows.map((r) => (r.entity_id === entityId ? { ...r, edges } : r)));
  }

  async function runRealNameSearch() {
    const q = realNameQuery.trim();
    if (!q || !workspace) return;
    setSearching(true);
    setSearchMessage(null);
    setHighlighted(new Set());
    try {
      const result = await searchByRealName(workspace, q);
      if ("locked" in result) {
        setSearchMessage("Access denied — re-identifier role required.");
        return;
      }
      const hitIds = new Set(result.hits.map((h) => h.entity_id));
      setHighlighted(hitIds);
      if (hitIds.size === 0) {
        setSearchMessage("No exact match in this workspace.");
      } else {
        setSearchMessage(null);
        const firstId = result.hits[0].entity_id;
        requestAnimationFrame(() => {
          document
            .querySelector(`[data-testid="entity-row-${firstId}"]`)
            ?.scrollIntoView({ block: "center" });
        });
      }
    } catch (e) {
      setSearchMessage(String(e));
    } finally {
      setSearching(false);
    }
  }

  if (!workspace) {
    return (
      <div className="bf-card">
        <h1>Entity list</h1>
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  const isEmptyWorkspace = !loading && !overCeiling && !error && allRows.length === 0;

  if (isEmptyWorkspace) {
    return (
      <div className="bf-card bf-entity-list" data-density={density}>
        <h1>Entity list</h1>
        <EntityListEmptyState workspace={workspace} onPopulated={setAllRows} />
      </div>
    );
  }

  return (
    <div className="bf-card bf-entity-list" data-density={density}>
      <h1>Entity list</h1>
      <div className="bf-entity-list-toolbar">
        <label className="bf-toolbar-field">
          <span>Kind</span>
          <select
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value as "" | "person" | "term")}
            data-testid="kind-filter"
          >
            <option value="">All</option>
            <option value="person">Person</option>
            <option value="term">Term</option>
          </select>
        </label>

        <div className="bf-search-mode-toggle" role="tablist" aria-label="Search mode">
          <button
            type="button"
            role="tab"
            aria-selected={searchMode === "surrogate"}
            className={`bf-search-mode-option${searchMode === "surrogate" ? " bf-search-mode-option--active" : ""}`}
            onClick={() => setSearchMode("surrogate")}
            data-testid="search-mode-surrogate"
          >
            Surrogate
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={searchMode === "real-name"}
            className={`bf-search-mode-option bf-search-mode-option--ochre${searchMode === "real-name" ? " bf-search-mode-option--active" : ""}`}
            onClick={() => setSearchMode("real-name")}
            data-testid="search-mode-real-name"
          >
            Real name
          </button>
        </div>

        {searchMode === "surrogate" ? (
          <input
            type="text"
            placeholder="Filter by surrogate…"
            value={surrogateFilter}
            onChange={(e) => setSurrogateFilter(e.target.value)}
            data-testid="surrogate-filter"
            className="bf-toolbar-input"
          />
        ) : canReveal ? (
          <form
            className="bf-real-name-search"
            onSubmit={(e) => {
              e.preventDefault();
              runRealNameSearch();
            }}
          >
            <input
              type="text"
              placeholder="Exact real name or known variation…"
              value={realNameQuery}
              onChange={(e) => setRealNameQuery(e.target.value)}
              data-testid="real-name-input"
              className="bf-toolbar-input bf-toolbar-input--ochre"
            />
            <button
              type="submit"
              disabled={searching || !realNameQuery.trim()}
              data-testid="real-name-search-btn"
              className="bf-btn-ochre"
            >
              Search
            </button>
            <span className="bf-real-name-hint">Exact match only. Logged as an audit event.</span>
          </form>
        ) : (
          <span className="bf-locked-msg" data-testid="real-name-search-locked">
            re-identifier role required
          </span>
        )}
      </div>

      {searchMessage && <div className="bf-search-message" data-testid="search-message">{searchMessage}</div>}
      {error && <div className="bf-error">{error}</div>}
      {overCeiling && (
        <div className="bf-ceiling-msg" data-testid="ceiling-message">
          More than {ENTITY_LIST_CEILING} entities — narrow with filters or use real-name search
          to find specific records.
        </div>
      )}

      {loading && <p className="bf-empty">Loading…</p>}

      {!loading && !overCeiling && !error && (
        <table className="bf-entity-table" data-testid="entity-table">
          <thead>
            <tr>
              <th onClick={() => toggleSort("active_surrogate")} data-testid="sort-surrogate">
                Surrogate {sortCol === "active_surrogate" ? (sortAsc ? "↑" : "↓") : "↕"}
              </th>
              <th onClick={() => toggleSort("kind")} data-testid="sort-kind">
                Kind {sortCol === "kind" ? (sortAsc ? "↑" : "↓") : "↕"}
              </th>
              <th onClick={() => toggleSort("employer")} data-testid="sort-edges">
                Edges {sortCol === "employer" ? (sortAsc ? "↑" : "↓") : "↕"}
              </th>
              <th onClick={() => toggleSort("retired_surrogates")} data-testid="sort-retired">
                Retired surrogates {sortCol === "retired_surrogates" ? (sortAsc ? "↑" : "↓") : "↕"}
              </th>
              <th>Real value</th>
              <th>Merge</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <EntityListRow
                key={row.entity_id}
                workspace={workspace}
                row={row}
                allRows={allRows}
                canReveal={canReveal}
                highlighted={highlighted.has(row.entity_id)}
                onRenamed={handleRenamed}
                onEdgesChanged={handleEdgesChanged}
                onStartMerge={(winner, loser) => setMergePair({ winner, loser })}
              />
            ))}
            {visibleRows.length === 0 && (
              <tr>
                <td colSpan={6} className="bf-empty">
                  No entities match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {mergePair && (
        <MergeDialog
          workspace={workspace}
          initialWinner={mergePair.winner}
          initialLoser={mergePair.loser}
          canReveal={canReveal}
          onClose={() => setMergePair(null)}
          onMerged={(loserId) => {
            setAllRows((rows) => rows.filter((r) => r.entity_id !== loserId));
            setMergePair(null);
          }}
        />
      )}
    </div>
  );
}

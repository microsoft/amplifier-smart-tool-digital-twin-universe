import type { Universe } from "@/lib/types";

export type SortKey = "id" | "name" | "state" | "created_at";
export type Sort = { key: SortKey; direction: "asc" | "desc" };

// Ascending runs from most alive to least, so "running" sorts first the way "a" would.
const STATE_ORDER: Record<Universe["state"], number> = {
  running: 0,
  starting: 1,
  degraded: 2,
  stopped: 3,
};

function compare(a: Universe, b: Universe, key: SortKey): number {
  switch (key) {
    case "state":
      return STATE_ORDER[a.state] - STATE_ORDER[b.state];
    case "created_at":
      return Date.parse(a.created_at) - Date.parse(b.created_at);
    default:
      return a[key].localeCompare(b[key]);
  }
}

export function sortUniverses(universes: Universe[], sort: Sort): Universe[] {
  const sign = sort.direction === "asc" ? 1 : -1;
  return [...universes].sort(
    (a, b) => sign * compare(a, b, sort.key) || a.id.localeCompare(b.id),
  );
}

/** Clicking the active column flips it; clicking another starts it ascending. */
export function toggleSort(sort: Sort, key: SortKey): Sort {
  if (sort.key !== key) {
    return { key, direction: "asc" };
  }
  return { key, direction: sort.direction === "asc" ? "desc" : "asc" };
}

/** Groups by profile name, in the order each name first appears in the sorted rows. */
export function groupByName(universes: Universe[]): [string, Universe[]][] {
  const groups = new Map<string, Universe[]>();
  for (const universe of universes) {
    groups.set(universe.name, [...(groups.get(universe.name) ?? []), universe]);
  }
  return [...groups.entries()];
}

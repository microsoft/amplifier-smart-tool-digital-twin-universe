import { useCallback, useEffect, useState } from "react";

import { Toolbar, type Filter } from "@/components/Toolbar";
import { UniversePanel } from "@/components/UniversePanel";
import { UniverseTable } from "@/components/UniverseTable";
import { describe, getUniverse, listUniverses, universesFrom } from "@/lib/api";
import { onSeed } from "@/lib/host";
import { sortUniverses, toggleSort, type Sort } from "@/lib/sort";
import type { Universe } from "@/lib/types";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;
const CLOCK_MS = 1000;

function matches(universe: Universe, search: string, filter: Filter): boolean {
  if (filter !== "all" && universe.state !== filter) {
    return false;
  }
  const needle = search.trim().toLowerCase();
  if (needle === "") {
    return true;
  }
  return [universe.id, universe.name, universe.description ?? ""].some((text) =>
    text.toLowerCase().includes(needle),
  );
}

function App() {
  const [universes, setUniverses] = useState<Universe[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [grouped, setGrouped] = useState(true);
  const [sort, setSort] = useState<Sort>({
    key: "created_at",
    direction: "desc",
  });
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setUniverses(await listUniverses());
      setError(null);
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setUpdatedAt(Date.now());
      setRefreshing(false);
    }
  }, []);

  useEffect(
    () =>
      onSeed(({ result, selectedId }) => {
        if (result !== undefined) {
          try {
            setUniverses(universesFrom(result));
            setUpdatedAt(Date.now());
          } catch (cause) {
            setError(describe(cause));
          }
        }
        if (selectedId !== undefined) {
          setSelectedId(selectedId);
        }
      }),
    [],
  );

  useEffect(() => {
    const initial = setTimeout(() => void refresh(), 0);
    const poll = setInterval(() => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    }, POLL_MS);
    const clock = setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => {
      clearTimeout(initial);
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [refresh]);

  async function check(id: string) {
    setChecking(true);
    try {
      const measured = await getUniverse(id);
      setUniverses((current) =>
        (current ?? []).map((universe) =>
          universe.id === id ? measured : universe,
        ),
      );
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setChecking(false);
    }
  }

  const selected =
    universes?.find((universe) => universe.id === selectedId) ?? null;
  const visible = sortUniverses(
    (universes ?? []).filter((universe) => matches(universe, search, filter)),
    sort,
  );
  const counts: Record<Filter, number> = {
    all: universes?.length ?? 0,
    running: 0,
    starting: 0,
    degraded: 0,
    stopped: 0,
  };
  for (const universe of universes ?? []) {
    counts[universe.state] += 1;
  }

  let emptyMessage = "Loading...";
  if (universes !== null) {
    emptyMessage =
      universes.length === 0
        ? "No universes yet. Launch one with `digital-twin-universe launch --profile hello`."
        : "No universes match.";
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-4 sm:p-8">
      <header className="text-sm font-semibold">Digital Twin Universe</header>

      <div>
        <h1 className="text-2xl font-semibold">Universes</h1>
        <p className="text-muted-foreground">
          Isolated environments launched from this machine.
        </p>
      </div>

      <Toolbar
        search={search}
        onSearch={setSearch}
        filter={filter}
        onFilter={setFilter}
        counts={counts}
        grouped={grouped}
        onGrouped={setGrouped}
        refreshing={refreshing}
        updatedAt={updatedAt}
        now={now}
        onRefresh={() => void refresh()}
      />

      {error !== null && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <div
        className={cn(
          "grid gap-6",
          selected !== null && "lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]",
        )}
      >
        <UniverseTable
          universes={visible}
          grouped={grouped}
          sort={sort}
          onSort={(key) => setSort(toggleSort(sort, key))}
          selectedId={selectedId}
          onSelect={(id) => setSelectedId(id === selectedId ? null : id)}
          now={now}
          emptyMessage={emptyMessage}
        />
        {selected !== null && (
          <UniversePanel
            universe={selected}
            now={now}
            checking={checking}
            onCheck={() => void check(selected.id)}
            onClose={() => setSelectedId(null)}
            onDestroyed={() => {
              setSelectedId(null);
              void refresh();
            }}
          />
        )}
      </div>
    </div>
  );
}

export default App;

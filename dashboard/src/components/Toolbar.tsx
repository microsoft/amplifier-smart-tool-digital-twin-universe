import { Layers, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { UniverseState } from "@/lib/types";
import { secondsAgo } from "@/lib/time";

export type Filter = "all" | UniverseState;

const FILTERS: Filter[] = ["all", "running", "starting", "degraded", "stopped"];

const LABELS: Record<Filter, string> = {
  all: "All",
  running: "Running",
  starting: "Starting",
  degraded: "Degraded",
  stopped: "Stopped",
};

export function Toolbar({
  search,
  onSearch,
  filter,
  onFilter,
  counts,
  grouped,
  onGrouped,
  refreshing,
  updatedAt,
  now,
  onRefresh,
}: {
  search: string;
  onSearch: (value: string) => void;
  filter: Filter;
  onFilter: (value: Filter) => void;
  counts: Record<Filter, number>;
  grouped: boolean;
  onGrouped: (value: boolean) => void;
  refreshing: boolean;
  updatedAt: number | null;
  now: number;
  onRefresh: () => void;
}) {
  const items = FILTERS.map((value) => ({
    value,
    label: `${LABELS[value]} ${counts[value]}`,
  }));
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative max-w-sm min-w-40 flex-1">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder="Search universes..."
          className="pl-8"
          aria-label="Search universes"
        />
      </div>
      <Select
        items={items}
        value={filter}
        onValueChange={(value) => onFilter(value as Filter)}
      >
        <SelectTrigger aria-label="Filter by state">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant={grouped ? "secondary" : "outline"}
        aria-pressed={grouped}
        onClick={() => onGrouped(!grouped)}
      >
        <Layers data-icon="inline-start" />
        Group by profile
      </Button>
      <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
        {updatedAt !== null && (
          <span>Updated {secondsAgo(updatedAt, now)}</span>
        )}
        <Button
          variant="outline"
          size="icon"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh"
        >
          <RefreshCw className={refreshing ? "animate-spin" : undefined} />
        </Button>
      </div>
    </div>
  );
}

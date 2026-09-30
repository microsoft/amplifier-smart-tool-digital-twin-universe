import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { StatusDot } from "@/components/StatusDot";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { groupByName, type Sort, type SortKey } from "@/lib/sort";
import { capitalize, toneForState } from "@/lib/status";
import { relativeTime } from "@/lib/time";
import type { Universe } from "@/lib/types";
import { cn } from "@/lib/utils";

const COLUMNS: [SortKey, string][] = [
  ["id", "ID"],
  ["name", "Profile"],
  ["state", "Status"],
  ["created_at", "Created"],
];

function SortableHead({
  column,
  label,
  sort,
  onSort,
}: {
  column: SortKey;
  label: string;
  sort: Sort;
  onSort: (key: SortKey) => void;
}) {
  const active = sort.key === column;
  return (
    <TableHead className="px-0">
      <button
        type="button"
        onClick={() => onSort(column)}
        aria-sort={
          active
            ? sort.direction === "asc"
              ? "ascending"
              : "descending"
            : undefined
        }
        className={cn(
          "inline-flex h-full items-center gap-1 px-2 font-medium hover:text-foreground",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {label}
        {active &&
          (sort.direction === "asc" ? (
            <ArrowUp className="size-3.5" />
          ) : (
            <ArrowDown className="size-3.5" />
          ))}
      </button>
    </TableHead>
  );
}

function UniverseRow({
  universe,
  selected,
  onSelect,
  now,
  indented,
}: {
  universe: Universe;
  selected: boolean;
  onSelect: (id: string) => void;
  now: number;
  indented: boolean;
}) {
  return (
    <TableRow
      onClick={() => onSelect(universe.id)}
      aria-selected={selected}
      className={cn(
        "cursor-pointer [&>td]:py-3",
        selected && "bg-primary/8 hover:bg-primary/10",
      )}
    >
      <TableCell className={indented ? "pl-8" : "pl-2"}>
        <span
          className={cn(
            "flex size-4 items-center justify-center rounded-full border",
            selected ? "border-primary" : "border-input",
          )}
        >
          {selected && <span className="size-2 rounded-full bg-primary" />}
        </span>
      </TableCell>
      <TableCell className="font-medium">{universe.id}</TableCell>
      <TableCell>{universe.name}</TableCell>
      <TableCell>
        <StatusDot
          tone={toneForState(universe.state)}
          label={capitalize(universe.state)}
        />
      </TableCell>
      <TableCell
        className="text-muted-foreground"
        title={new Date(universe.created_at).toLocaleString()}
      >
        {relativeTime(universe.created_at, now)}
      </TableCell>
    </TableRow>
  );
}

export function UniverseTable({
  universes,
  grouped,
  sort,
  onSort,
  selectedId,
  onSelect,
  now,
  emptyMessage,
}: {
  universes: Universe[];
  grouped: boolean;
  sort: Sort;
  onSort: (key: SortKey) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  now: number;
  emptyMessage: string;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  function toggleGroup(name: string) {
    const next = new Set(collapsed);
    if (!next.delete(name)) {
      next.add(name);
    }
    setCollapsed(next);
  }

  const rows = (list: Universe[], indented: boolean) =>
    list.map((universe) => (
      <UniverseRow
        key={universe.id}
        universe={universe}
        selected={universe.id === selectedId}
        onSelect={onSelect}
        now={now}
        indented={indented}
      />
    ));

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-12" />
          {COLUMNS.map(([column, label]) => (
            <SortableHead
              key={column}
              column={column}
              label={label}
              sort={sort}
              onSort={onSort}
            />
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {universes.length === 0 && (
          <TableRow className="hover:bg-transparent">
            <TableCell
              colSpan={5}
              className="py-10 text-center text-muted-foreground"
            >
              {emptyMessage}
            </TableCell>
          </TableRow>
        )}
        {!grouped && rows(universes, false)}
        {grouped &&
          groupByName(universes).map(([name, members]) => {
            const open = !collapsed.has(name);
            return [
              <TableRow
                key={`group:${name}`}
                onClick={() => toggleGroup(name)}
                className="cursor-pointer bg-muted/30 hover:bg-muted/50"
              >
                <TableCell colSpan={5} className="py-2">
                  <span className="inline-flex items-center gap-2 font-medium">
                    {open ? (
                      <ChevronDown className="size-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="size-4 text-muted-foreground" />
                    )}
                    {name}
                    <span className="font-normal text-muted-foreground">
                      {members.length}
                    </span>
                  </span>
                </TableCell>
              </TableRow>,
              ...(open ? rows(members, true) : []),
            ];
          })}
      </TableBody>
    </Table>
  );
}

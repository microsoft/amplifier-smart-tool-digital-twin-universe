import type { CallToolResult } from "@modelcontextprotocol/client";

import { app } from "@/lib/host";
import type { Destroyed, Universe } from "@/lib/types";

// The MCP SDK prefixes every tool failure; the rest is the library's message and remedy.
const TOOL_ERROR_PREFIX = /^Error executing tool \S+: /;

export class DashboardError extends Error {}

function structured<T>(name: string, result: CallToolResult): T {
  if (result.isError) {
    const text = result.content
      .flatMap((block) => (block.type === "text" ? [block.text] : []))
      .join(" ")
      .replace(TOOL_ERROR_PREFIX, "");
    throw new DashboardError(text || `\`${name}\` failed without a message.`);
  }
  if (result.structuredContent === undefined) {
    throw new DashboardError(
      `\`${name}\` returned no structured content. Check that the server is Digital Twin Universe.`,
    );
  }
  return result.structuredContent as T;
}

/** The universes in a result of `list_universes` or `open_dashboard`. */
export function universesFrom(
  result: CallToolResult,
  name = "open_dashboard",
): Universe[] {
  return structured<{ result: Universe[] }>(name, result).result;
}

async function call(
  name: string,
  args: Record<string, unknown> = {},
): Promise<CallToolResult> {
  return app.callServerTool({ name, arguments: args });
}

export async function listUniverses(): Promise<Universe[]> {
  return universesFrom(await call("list_universes"), "list_universes");
}

export async function getUniverse(id: string): Promise<Universe> {
  return structured<Universe>(
    "universe_status",
    await call("universe_status", { id }),
  );
}

export async function destroyUniverse(id: string): Promise<Destroyed> {
  const result = structured<Destroyed>(
    "destroy_universe",
    await call("destroy_universe", { id }),
  );
  const remaining = result.remaining ?? {};
  if (
    result.outcome !== "removed" ||
    result.state_removed !== true ||
    Object.keys(result.errors ?? {}).length !== 0 ||
    Object.keys(remaining).length !== 3 ||
    !["containers", "networks", "volumes"].every(
      (kind) => Array.isArray(remaining[kind]) && remaining[kind]?.length === 0,
    )
  ) {
    throw new DashboardError(
      `Cleanup of ${id} is incomplete: ${JSON.stringify(result)}. Inspect the reported resources and errors.`,
    );
  }
  return result;
}

export function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

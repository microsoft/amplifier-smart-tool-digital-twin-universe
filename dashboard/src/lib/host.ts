import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/client";

export const app = new App({
  name: "Digital Twin Universe dashboard",
  version: "1.0.0",
});

/** What the `open_dashboard` call that opened this view delivered. */
export type Seed = { result?: CallToolResult; selectedId?: string };

let seed: Seed = {};
const seedListeners = new Set<(patch: Seed) => void>();

function updateSeed(patch: Seed) {
  seed = { ...seed, ...patch };
  for (const listener of seedListeners) {
    listener(patch);
  }
}

/** Calls `listener` with the seed so far, then with each later part of it. */
export function onSeed(listener: (patch: Seed) => void): () => void {
  listener(seed);
  seedListeners.add(listener);
  return () => {
    seedListeners.delete(listener);
  };
}

function applyHostContext(context: McpUiHostContext) {
  if (context.theme) {
    applyDocumentTheme(context.theme);
  }
  if (context.styles?.variables) {
    applyHostStyleVariables(context.styles.variables);
  }
  if (context.styles?.css?.fonts) {
    applyHostFonts(context.styles.css.fonts);
  }
  const insets = context.safeAreaInsets;
  if (insets) {
    document.body.style.padding = `${insets.top}px ${insets.right}px ${insets.bottom}px ${insets.left}px`;
  }
}

// The host sends these once, right after the handshake, so they must be registered before `connect`.
app.addEventListener("toolinput", ({ arguments: args }) => {
  if (typeof args?.id === "string") {
    updateSeed({ selectedId: args.id });
  }
});
app.addEventListener("toolresult", (result) => updateSeed({ result }));
app.addEventListener("hostcontextchanged", applyHostContext);

export async function connect(): Promise<void> {
  if (window.parent === window) {
    throw new Error(
      "This page is an MCP App view and needs a host. Call `open_dashboard` from an MCP client that supports MCP Apps.",
    );
  }
  await app.connect();
  const context = app.getHostContext();
  if (context) {
    applyHostContext(context);
  }
}

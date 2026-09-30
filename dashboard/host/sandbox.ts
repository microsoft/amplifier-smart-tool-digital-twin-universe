import {
  buildAllowAttribute,
  type McpUiSandboxProxyReadyNotification,
  type McpUiSandboxResourceReadyNotification,
} from "@modelcontextprotocol/ext-apps/app-bridge";

const RESOURCE_READY: McpUiSandboxResourceReadyNotification["method"] =
  "ui/notifications/sandbox-resource-ready";
const PROXY_READY: McpUiSandboxProxyReadyNotification["method"] =
  "ui/notifications/sandbox-proxy-ready";

function hostOrigin(): string {
  const port = document
    .querySelector<HTMLMetaElement>(
      'meta[name="digital-twin-universe-host-port"]',
    )
    ?.content.trim();
  const expected = `${location.protocol}//${location.hostname}:${port}`;
  // Only the dashboard page served beside this one may embed it; the server fills in its port, so it cannot be forged.
  if (
    window.self === window.top ||
    !port ||
    !document.referrer ||
    new URL(document.referrer).origin !== expected
  ) {
    const reason = `This page only runs inside the dashboard at ${expected}.`;
    const notice = document.createElement("p");
    notice.textContent = reason;
    notice.style.cssText =
      "margin: 0; padding: 1rem; font: 14px system-ui, sans-serif;";
    document.body.append(notice);
    throw new Error(reason);
  }
  return expected;
}

const host = hostOrigin();
const inner = document.createElement("iframe");
inner.title = "Digital Twin Universe";
inner.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
document.body.append(inner);

// A relay between the host page and the view, which cannot reach each other across origins. The view is written into
// the inner frame, so it inherits the CSP this page was served with.
window.addEventListener("message", ({ source, origin, data }) => {
  if (source === window.parent && origin === host) {
    if (data?.method !== RESOURCE_READY) {
      inner.contentWindow?.postMessage(data, "*");
      return;
    }
    const { html, sandbox, permissions } = data.params;
    if (typeof sandbox === "string") {
      inner.setAttribute("sandbox", sandbox);
    }
    const allow = buildAllowAttribute(permissions);
    if (allow) {
      inner.setAttribute("allow", allow);
    }
    const doc = inner.contentDocument!;
    doc.open();
    doc.write(html);
    doc.close();
  } else if (source === inner.contentWindow && origin === location.origin) {
    window.parent.postMessage(data, host);
  }
});

window.parent.postMessage(
  { jsonrpc: "2.0", method: PROXY_READY, params: {} },
  host,
);

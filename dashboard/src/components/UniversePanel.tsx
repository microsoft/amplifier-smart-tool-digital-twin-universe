import { Check, Copy, ExternalLink, Info, X } from "lucide-react";
import { useRef, useState } from "react";

import { DestroyDialog } from "@/components/DestroyDialog";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { app } from "@/lib/host";
import { capitalize } from "@/lib/status";
import { relativeTime } from "@/lib/time";
import type { Universe } from "@/lib/types";

function readiness(universe: Universe) {
  const checked = universe.services.filter(
    (service) => service.health !== null,
  );
  const failing = checked.filter((service) => service.health !== "healthy");
  return {
    checked,
    failing,
    ready: checked.length > 0 && failing.length === 0,
  };
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 text-sm">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="text-right break-all">{value}</span>
    </div>
  );
}

function CopyText({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const textRef = useRef<HTMLSpanElement>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setBlocked(true);
      if (textRef.current !== null) {
        window.getSelection()?.selectAllChildren(textRef.current);
      }
    }
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 rounded-md bg-muted px-2.5 py-1.5 font-mono text-xs">
        <span
          ref={textRef}
          className={
            blocked ? "flex-1 break-all select-all" : "flex-1 truncate"
          }
          title={text}
        >
          {text}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={copy}
          aria-label={label}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
      {blocked && (
        <p className="text-xs text-muted-foreground">
          The host blocked copying. The text is selected; copy it yourself.
        </p>
      )}
    </div>
  );
}

function Link({ url, label }: { url: string; label: string }) {
  const [refused, setRefused] = useState(false);
  async function open(event: React.MouseEvent) {
    // Views run in a sandboxed iframe, so only the host can open a new tab.
    event.preventDefault();
    try {
      const { isError } = await app.openLink({ url });
      setRefused(Boolean(isError));
    } catch {
      setRefused(true);
    }
  }
  return (
    <div className="flex flex-col text-sm">
      <a
        href={url}
        onClick={open}
        className="inline-flex items-center gap-1 text-primary hover:underline"
      >
        {label}
        <ExternalLink className="size-3.5" />
      </a>
      {refused ? (
        <CopyText text={url} label="Copy URL" />
      ) : (
        <span className="text-muted-foreground">
          {url.replace(/^https?:\/\//, "")}
        </span>
      )}
    </div>
  );
}

export function UniversePanel({
  universe,
  now,
  checking,
  onCheck,
  onClose,
  onDestroyed,
}: {
  universe: Universe;
  now: number;
  checking: boolean;
  onCheck: () => void;
  onClose: () => void;
  onDestroyed: () => void;
}) {
  const twin = universe.services.find(
    (service) => service.name === universe.twin_machine,
  );
  const { checked, failing, ready } = readiness(universe);
  const created = new Date(universe.created_at);

  return (
    <aside className="flex flex-col gap-5 self-start rounded-lg border p-6">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-lg font-semibold break-all">{universe.id}</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClose}
          aria-label="Close"
        >
          <X />
        </Button>
      </div>

      <div className="flex flex-col gap-1">
        <Row label="Profile" value={universe.name} />
        {universe.description && (
          <p className="text-sm text-muted-foreground">
            {universe.description}
          </p>
        )}
      </div>

      <Separator />

      <div className="flex flex-col gap-2">
        <Row
          label="Container"
          value={
            <StatusDot
              tone={twin?.state === "running" ? "green" : "gray"}
              label={twin ? capitalize(twin.state) : "Not present"}
              className="font-medium"
            />
          }
        />
        <Row
          label="Readiness"
          value={
            <StatusDot
              tone={ready ? "green" : checked.length === 0 ? "gray" : "orange"}
              label={
                checked.length === 0
                  ? "No healthchecks"
                  : ready
                    ? "Ready"
                    : "Not ready"
              }
              className="font-medium"
            />
          }
        />
        {checked.length > 0 && !ready && (
          <div className="flex items-start gap-2 rounded-md border border-orange-200 bg-orange-50 p-3 text-sm dark:border-orange-900 dark:bg-orange-950/40">
            <Info className="mt-0.5 size-4 shrink-0 text-orange-500" />
            <div className="flex-1">
              <p className="font-medium text-orange-900 dark:text-orange-200">
                {checked.length - failing.length} of {checked.length} checks
                passed
              </p>
              {failing.map((service) => (
                <p key={service.name} className="text-muted-foreground">
                  {service.name} &middot; {service.health}
                </p>
              ))}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={onCheck}
              disabled={checking}
            >
              {checking ? "Checking..." : "Check again"}
            </Button>
          </div>
        )}
      </div>

      <Separator />

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Access</h3>
        {universe.urls.length === 0 && (
          <p className="text-sm text-muted-foreground">No published ports.</p>
        )}
        {universe.urls.map((url) => (
          <Link key={url.url} url={url.url} label={url.label ?? url.path} />
        ))}
      </div>

      <Separator />

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Details</h3>
        <Row
          label="Created"
          value={
            <span title={created.toLocaleString()}>
              {relativeTime(universe.created_at, now, true)}
            </span>
          }
        />
        <Row label="Twin" value={universe.twin_machine} />
        <Row label="Profile path" value={universe.profile_path} />
        <Row label="State path" value={universe.state_path} />
        <CopyText
          text={`digital-twin-universe exec --id ${universe.id}`}
          label="Copy command"
        />
      </div>

      <Separator />

      <DestroyDialog id={universe.id} onDestroyed={onDestroyed} />
    </aside>
  );
}

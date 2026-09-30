import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { describe, destroyUniverse } from "@/lib/api";

export function DestroyDialog({
  id,
  onDestroyed,
}: {
  id: string;
  onDestroyed: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function destroy() {
    setBusy(true);
    setError(null);
    try {
      await destroyUniverse(id);
      setOpen(false);
      onDestroyed();
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            variant="outline"
            className="w-full border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          />
        }
      >
        Destroy universe...
      </DialogTrigger>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Destroy {id}?</DialogTitle>
          <DialogDescription>
            Every container, network, and volume of this universe is removed,
            along with its state. Built images stay.
          </DialogDescription>
        </DialogHeader>
        {error !== null && <p className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button variant="destructive" onClick={destroy} disabled={busy}>
            {busy ? "Destroying..." : "Destroy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

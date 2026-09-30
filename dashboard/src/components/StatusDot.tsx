import type { Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

const TONES: Record<Tone, string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-400",
  orange: "bg-orange-500",
  gray: "bg-zinc-400",
};

export function StatusDot({
  tone,
  label,
  className,
}: {
  tone: Tone;
  label: string;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className={cn("size-2 shrink-0 rounded-full", TONES[tone])} />
      {label}
    </span>
  );
}

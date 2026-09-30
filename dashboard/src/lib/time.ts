const UNITS: [short: string, long: string, seconds: number][] = [
  ["day", "day", 86400],
  ["hr", "hour", 3600],
  ["min", "minute", 60],
];

/** "12 min ago" in the table; "12 minutes ago" when `long`; "just now" under a minute. */
export function relativeTime(iso: string, now: number, long = false): string {
  const seconds = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
  for (const [short, full, size] of UNITS) {
    if (seconds >= size) {
      const count = Math.floor(seconds / size);
      const unit = long ? full : short;
      const plural = count !== 1 && (long || unit === "day") ? "s" : "";
      return `${count} ${unit}${plural} ago`;
    }
  }
  return "just now";
}

/** "3s ago" for the refresh indicator. */
export function secondsAgo(at: number, now: number): string {
  return `${Math.max(0, Math.floor((now - at) / 1000))}s ago`;
}

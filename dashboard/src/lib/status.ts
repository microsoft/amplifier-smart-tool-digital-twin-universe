export type Tone = "green" | "amber" | "orange" | "gray";

export function toneForState(state: string): Tone {
  switch (state) {
    case "running":
      return "green";
    case "starting":
      return "amber";
    case "degraded":
      return "orange";
    default:
      return "gray";
  }
}

export function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

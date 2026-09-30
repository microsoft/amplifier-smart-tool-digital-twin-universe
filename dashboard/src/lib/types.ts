// Mirrors the result classes in src/digital_twin_universe/schemas.py; the MCP tools return them unchanged.

export type UniverseState = "starting" | "running" | "degraded" | "stopped";
export type Health = "starting" | "healthy" | "unhealthy";

export type Service = {
  name: string;
  state: string;
  health: Health | null;
  image: string;
};

export type Url = {
  url: string;
  port: number;
  path: string;
  label: string | null;
};

export type Universe = {
  id: string;
  name: string;
  description: string | null;
  profile_path: string;
  twin_machine: string;
  state: UniverseState;
  services: Service[];
  urls: Url[];
  state_path: string;
  created_at: string;
};

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { describe } from "@/lib/api";
import { connect } from "@/lib/host";

const root = createRoot(document.getElementById("root")!);

try {
  await connect();
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
} catch (cause) {
  root.render(
    <div className="m-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
      Could not connect to the host. {describe(cause)}
    </div>,
  );
}

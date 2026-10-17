import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { useStore } from "./lib/store";
import "./tokens.css";
import "./styles.css";
import "./phone.css";

// Dev server only: lets the screenshot harness (scripts/ui_screens.mjs) seed demo state.
if (import.meta.env.DEV) (window as unknown as { __orion: typeof useStore }).__orion = useStore;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

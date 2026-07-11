import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./styles/tokens.css";
import "./styles/shell.css";

import { App } from "./App";

createRoot(document.getElementById("bf-shell-root")!).render(
  <StrictMode>
    <BrowserRouter basename="/ui">
      <App />
    </BrowserRouter>
  </StrictMode>
);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles/tokens.css";
import "./styles/app.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root is missing from index.html");
}

createRoot(container).render(
  <StrictMode>
    {/*
     * The boundary is OUTSIDE App so a render error inside any panel still leaves
     * a legible page. Without it, React unmounts the whole tree and the operator
     * gets a blank screen with the cause in a console they are not looking at.
     */}
    <ErrorBoundary label="The command center">
      <App />
    </ErrorBoundary>
  </StrictMode>,
);

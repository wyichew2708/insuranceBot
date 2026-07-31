import { createRoot } from "react-dom/client";
import { App } from "./App";

const rootEl = document.getElementById("root");
if (rootEl) {
  // Brand comes from the embedding page in dev; in production it is bound
  // server-side to the widget key and this attribute is ignored (§9.1).
  const brand = rootEl.dataset.brand === "etiqa" ? "etiqa" : "tiq";
  createRoot(rootEl).render(<App brand={brand} locale="en" />);
}

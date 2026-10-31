import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./cards/cards.css";
import App from "./App.tsx";
import { Gallery } from "./cards/Gallery.tsx";

// /?gallery shows every card and state from the mocks, for visual review.
const gallery = new URLSearchParams(window.location.search).has("gallery");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {gallery ? (
      <div className="app">
        <Gallery />
      </div>
    ) : (
      <App />
    )}
  </StrictMode>,
);

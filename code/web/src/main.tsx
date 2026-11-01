import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import "./index.css";
import "./cards/cards.css";
import "./studio/fonts.css";
import { leaveTransitionsToParent } from "./management/motion.ts";
import { router } from "./router.tsx";

leaveTransitionsToParent();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);

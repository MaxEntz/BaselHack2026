import { Navigate, createBrowserRouter } from "react-router";
import { ManagePage } from "./management/ManagePage.tsx";
import { Studio } from "./Studio.tsx";

export const routes = [
  { path: "/", element: <Studio /> },
  { path: "/manage/:slug", element: <ManagePage /> },
  { path: "/manage/:slug/:page", element: <ManagePage /> },
  { path: "/manage/:slug/*", element: <ManagePage /> },
  { path: "*", element: <Navigate to="/" replace /> },
];

export const router = createBrowserRouter(routes);

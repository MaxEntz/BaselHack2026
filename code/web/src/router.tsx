import { Navigate, type RouteObject, createBrowserRouter } from "react-router";
import { ManagePage } from "./management/ManagePage.tsx";
import { RouteError } from "./RouteError.tsx";
import { Studio } from "./Studio.tsx";

export const routes: RouteObject[] = [
  {
    // A page that fails to draw shows this, not React Router's developer screen (#220).
    errorElement: <RouteError />,
    children: [
      { path: "/", element: <Studio /> },
      { path: "/manage/:slug", element: <ManagePage /> },
      { path: "/manage/:slug/:page", element: <ManagePage /> },
      { path: "/manage/:slug/*", element: <ManagePage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
];

export const router = createBrowserRouter(routes);

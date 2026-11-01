import type { ReactNode } from "react";
import { Navigate, type RouteObject, createBrowserRouter } from "react-router";
// Before Studio: the mock build inlines the dynamic imports in this order, so that its one
// stylesheet keeps the order the screenshots were taken with (vite.config.ts).
import { LazyManagePage } from "./management/LazyManagePage.tsx";
import { RouteError } from "./RouteError.tsx";
import { Studio } from "./Studio.tsx";

/** The routes, `manage` drawing every management page: lazy in the app, the page itself in tests. */
export function routesWith(manage: ReactNode): RouteObject[] {
  return [
    {
      // A page that fails to draw shows this, not React Router's developer screen (#220).
      errorElement: <RouteError />,
      children: [
        { path: "/", element: <Studio /> },
        { path: "/manage/:slug", element: manage },
        { path: "/manage/:slug/:page", element: manage },
        { path: "/manage/:slug/*", element: manage },
        { path: "*", element: <Navigate to="/" replace /> },
      ],
    },
  ];
}

export const routes = routesWith(<LazyManagePage />);

export const router = createBrowserRouter(routes);

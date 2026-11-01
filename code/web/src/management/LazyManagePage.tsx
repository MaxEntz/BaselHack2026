import { Suspense, lazy } from "react";

// The management site loads on its first visit, so that Studio's chunk doesn't carry it (#220).
const Page = lazy(async () => ({ default: (await import("./ManagePage.tsx")).ManagePage }));

/** Every management page, once its chunk is in. */
export function LazyManagePage() {
  return (
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  );
}

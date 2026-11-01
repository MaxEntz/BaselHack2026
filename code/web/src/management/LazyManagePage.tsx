import { Suspense, lazy } from "react";

// The management site loads on its first visit, so that Studio's chunk doesn't carry it (#220).
// No prefetch from Studio: its frame is another document, and importing the chunk in Studio's
// would run it and attach its stylesheet there too, for one request the frame saves.
const Page = lazy(async () => ({ default: (await import("./ManagePage.tsx")).ManagePage }));

/** Every management page, once its chunk is in; until then, a word for screen readers. */
export function LazyManagePage() {
  return (
    <Suspense
      fallback={
        <p className="st-sr" role="status">
          Opening your management site…
        </p>
      }
    >
      <Page />
    </Suspense>
  );
}

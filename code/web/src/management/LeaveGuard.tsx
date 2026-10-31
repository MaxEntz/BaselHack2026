import { useEffect } from "react";
import { useBlocker } from "react-router";
import { Dialog, Pill } from "./ui/index.ts";

/** Asks before leaving a page with unsaved changes, in the site or out of it. */
export function LeaveGuard({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const stay = () => blocker.reset?.();
  return (
    <Dialog open={blocker.state === "blocked"} onClose={stay} title="Leave without saving?">
      <p>Your changes on this page aren't saved yet.</p>
      <div className="dlg-foot">
        <Pill small onClick={stay}>
          Keep editing
        </Pill>
        <Pill small primary onClick={() => blocker.proceed?.()}>
          Leave
        </Pill>
      </div>
    </Dialog>
  );
}

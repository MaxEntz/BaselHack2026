import type { ReactNode } from "react";
import { Icon } from "../ui/index.ts";

/**
 * The assistant's column: beside the page above 1240 px, an overlay below, the whole page on
 * Assistant. It stays mounted while hidden, so the conversation (#140) survives a close.
 */
export function AssistantPanel({
  shown,
  full,
  onResize,
  onClose,
  children,
}: {
  shown: boolean;
  // On the Assistant page.
  full: boolean;
  onResize: () => void;
  onClose: () => void;
  children?: ReactNode;
}) {
  return (
    <aside className="asst" aria-label="Assistant" hidden={!shown}>
      <div className="asst-hd">
        <h2>Assistant</h2>
        <button
          className="x"
          type="button"
          aria-label={full ? "Back to the dashboard" : "Open the assistant on a full page"}
          onClick={onResize}
        >
          <Icon name={full ? "shrink" : "expand"} />
        </button>
        <button className="x asst-close" type="button" aria-label="Close the assistant" onClick={onClose}>
          <Icon name="x" />
        </button>
      </div>
      {children}
    </aside>
  );
}

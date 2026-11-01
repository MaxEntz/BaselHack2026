import type { Pane } from "./useCanvasView.ts";

type PaneSwitchProps = {
  pane: Pane;
  onPane: (pane: Pane) => void;
};

/** Up to `NARROW`, one pane at a time: the conversation or the sites. */
export function PaneSwitch({ pane, onPane }: PaneSwitchProps) {
  return (
    <div className="st-pane-switch" role="group" aria-label="Pane">
      <button type="button" aria-pressed={pane === "chat"} onClick={() => onPane("chat")}>
        Conversation
      </button>
      <button type="button" aria-pressed={pane === "canvas"} onClick={() => onPane("canvas")}>
        Your sites
      </button>
    </div>
  );
}

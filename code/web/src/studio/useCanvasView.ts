import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatItem } from "../chat/state.ts";
import { crossfade } from "../management/motion.ts";
import type { BuildAgentOutput, BuildFact, Moment } from "../types.ts";
import type { View } from "./feed.tsx";
import { useReviewPlayback } from "./useReviewPlayback.ts";

export type Pane = "chat" | "canvas";

type CanvasInput = {
  items: readonly ChatItem[];
  profile: BuildAgentOutput | null;
  // The pages the canvas can show: the ones the crawl read, or the stored ones.
  pageCount: number;
  // The crawl sent its first page in this conversation.
  firstPage: boolean;
};

export type CanvasState = {
  view: View;
  fact: BuildFact | null;
  moment: Moment | null;
  // Up to `NARROW`, the pane in front.
  pane: Pane;
  setPane: (pane: Pane) => void;
  showView: (next: View) => void;
  showFact: (item: BuildFact) => void;
  showMoment: (next: Moment) => void;
  // A view, in front, from the feed or the assistant.
  open: (next: View, nextMoment?: Moment | null) => void;
  // The merchant does something else: the playback stops, the lit fact goes out.
  quiet: () => void;
  // A new conversation; call it inside its crossfade.
  clear: () => void;
};

/** What Studio's canvas shows, and what changes it. */
export function useCanvasView({ items, profile, pageCount, firstPage }: CanvasInput): CanvasState {
  const [view, setView] = useState<View>("manage");
  const [fact, setFact] = useState<BuildFact | null>(null);
  const [moment, setMoment] = useState<Moment | null>(null);
  const [pane, setPane] = useState<Pane>("chat");
  const stop = useReviewPlayback(profile, pageCount, setView, setFact);

  // The first page read shows as soon as it arrives.
  const [hadPage, setHadPage] = useState(false);
  if (firstPage !== hadPage) {
    setHadPage(firstPage);
    if (firstPage) setView("public");
  }

  const open = useCallback(
    (next: View, nextMoment?: Moment | null) => {
      stop();
      if (nextMoment) setMoment(nextMoment);
      crossfade(() => {
        setFact(null);
        setView(next);
        setPane("canvas");
      });
    },
    [stop],
  );
  useAssistantViews(items, open);

  return {
    view,
    fact,
    moment,
    pane,
    setPane,
    open,
    showView(next) {
      stop();
      crossfade(() => {
        setFact(null);
        setView(next);
      });
    },
    showFact(item) {
      stop();
      if (pageCount === 0) return;
      crossfade(() => {
        setView("public");
        setPane("canvas");
        setFact(item);
      });
    },
    // The dock's moment, or the chat's: on the management site, and again to go back to the clock.
    showMoment(next) {
      stop();
      setMoment((current) => (current === next ? null : next));
      if (view !== "manage" && view !== "both") crossfade(() => setView("manage"));
    },
    quiet() {
      stop();
      setFact(null);
    },
    clear() {
      setMoment(null);
      setFact(null);
      stop();
      setPane("chat");
      setView("manage");
    },
  };
}

// The assistant's `show_view` (#152): each view card switches the canvas, once, also when
// StrictMode runs the effect twice. A new conversation counts its ids from 1 again
// (`chat/state.ts`), so the card shown is forgotten once the conversation holds none.
function useAssistantViews(items: readonly ChatItem[], open: CanvasState["open"]): void {
  const asked = items.findLast((item) => item.type === "card" && item.card.kind === "view");
  const card = asked?.type === "card" && asked.card.kind === "view" ? asked.card.data : null;
  const askedId = asked?.id ?? null;
  const askedView = card?.view ?? null;
  const askedMoment = card?.moment ?? null;
  const shown = useRef<string | null>(null);
  useEffect(() => {
    if (askedId === null) {
      shown.current = null;
      return;
    }
    if (askedView === null || shown.current === askedId) return;
    shown.current = askedId;
    open(askedView, askedMoment);
  }, [askedId, askedView, askedMoment, open]);
}

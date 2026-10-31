import { useLayoutEffect, useRef } from "react";
import type { ChatItem } from "../chat/state.ts";
import { ChatItemView } from "./ChatItemView.tsx";

// Distance from the bottom under which the list keeps following new messages.
const STICK_THRESHOLD_PX = 80;

type Props = {
  items: readonly ChatItem[];
  activity: string | null;
  busy: boolean;
};

export function MessageList({ items, activity, busy }: Props) {
  const list = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    // A message sent by the merchant always brings the view back to the bottom.
    if (items.at(-1)?.type === "user") stick.current = true;
    const element = list.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  }, [items, activity, busy]);

  function onScroll() {
    const element = list.current;
    if (!element) return;
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    stick.current = distance < STICK_THRESHOLD_PX;
  }

  return (
    <div className="messages" ref={list} onScroll={onScroll} aria-live="polite">
      <div className="messages-inner">
        {items.map((item) => (
          <ChatItemView key={item.id} item={item} />
        ))}
        {busy && (
          <div className="activity" role="status">
            <span className="typing" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            {activity}
          </div>
        )}
      </div>
    </div>
  );
}

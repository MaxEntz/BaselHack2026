import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import { CardView } from "../cards/CardView.tsx";
import { ErrorItem } from "../cards/ErrorItem.tsx";
import { NotificationItem } from "../cards/NotificationItem.tsx";
import { StepsCard } from "../cards/StepsCard.tsx";
import type { ChatItem } from "../chat/state.ts";

// Links in assistant replies open next to the chat, never in place of it.
const MARKDOWN_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
};

type Props = {
  item: ChatItem;
  onRetry?: () => void;
};

export function ChatItemView({ item, onRetry }: Props) {
  switch (item.type) {
    case "user":
      return <div className="bubble bubble-user">{item.text}</div>;
    case "assistant":
      return (
        <div className="bubble bubble-assistant">
          <Markdown components={MARKDOWN_COMPONENTS}>{item.text}</Markdown>
        </div>
      );
    case "steps":
      return <StepsCard steps={item.steps} />;
    case "card":
      return <CardView card={item.card} />;
    case "notification":
      return <NotificationItem event={item.event} />;
    case "error":
      return <ErrorItem message={item.message} onRetry={onRetry} />;
  }
}

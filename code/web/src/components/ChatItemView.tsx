import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import type { ChatItem } from "../chat/state.ts";

// Links in assistant replies open next to the chat, never in place of it.
const MARKDOWN_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
};

export function ChatItemView({ item }: { item: ChatItem }) {
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
      return (
        <div className="card">
          <ul className="plain-steps">
            {Object.entries(item.steps).map(([step, state]) => (
              <li key={step}>
                {step}: {state.status}
                {state.detail && ` — ${state.detail}`}
              </li>
            ))}
          </ul>
        </div>
      );
    case "card":
      // Placeholder until the dedicated cards land (#20).
      return (
        <div className="card">
          <strong>{item.card.kind}</strong>
          <pre>{JSON.stringify(item.card.data, null, 2)}</pre>
        </div>
      );
    case "notification": {
      const { kind, reservation } = item.event;
      return (
        <div className="notification" role="alert">
          🔔 {kind.replace("_", " ")} — {reservation.id} · {reservation.party_size}{" "}
          guests · {reservation.time}
        </div>
      );
    }
    case "error":
      return (
        <div className="error-item" role="alert">
          {item.message}
        </div>
      );
  }
}

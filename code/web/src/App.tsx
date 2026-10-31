import { httpClient } from "./chat/client.ts";
import type { Merchant } from "./chat/state.ts";
import { useChat } from "./chat/useChat.ts";
import { Composer } from "./components/Composer.tsx";
import { Header } from "./components/Header.tsx";
import { MessageList } from "./components/MessageList.tsx";
import { createMockClient } from "./mocks/mockClient.ts";

const env = import.meta.env;
const DEMO_MERCHANT: Merchant | null = env.VITE_MERCHANT_SLUG
  ? {
      slug: env.VITE_MERCHANT_SLUG,
      name: env.VITE_MERCHANT_NAME || env.VITE_MERCHANT_SLUG,
    }
  : null;
const CLIENT =
  env.VITE_MOCK === "1" ? createMockClient(DEMO_MERCHANT !== null) : httpClient;

const WELCOME = DEMO_MERCHANT
  ? "Welcome back! Ask me about tonight's bookings, block a time slot or change your booking policy."
  : "Hi! I'm your platform assistant. I'll turn your website into a booking agent that any AI assistant can talk to.\n\n**What's your restaurant's website?**";

export default function App() {
  const { state, send, liveConnected } = useChat(CLIENT, WELCOME, DEMO_MERCHANT);
  const lastMessage = state.items.findLast((item) => item.type === "user");

  return (
    <div className="app">
      <Header
        merchant={state.merchant}
        status={state.status}
        liveConnected={liveConnected}
      />
      <MessageList
        items={state.items}
        activity={state.activity}
        busy={state.busy}
        onRetry={() => lastMessage && void send(lastMessage.text)}
      />
      <Composer disabled={state.busy} onSend={(text) => void send(text)} />
    </div>
  );
}

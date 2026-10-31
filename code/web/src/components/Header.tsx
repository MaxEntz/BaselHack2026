import type { AgentStatus, Merchant } from "../chat/state.ts";

const STATUS_LABELS: Record<AgentStatus, string> = {
  building: "Building",
  live: "Live",
  down: "Down",
};

type Props = {
  merchant: Merchant | null;
  status: AgentStatus | null;
  liveConnected: boolean;
};

export function Header({ merchant, status, liveConnected }: Props) {
  return (
    <header className="header">
      <div className="header-title">
        <h1>{merchant?.name ?? "New merchant"}</h1>
        {status && (
          <span className={`pill pill-${status}`}>
            Agent: {STATUS_LABELS[status]}
          </span>
        )}
      </div>
      {merchant && (
        <span
          className={`live-indicator ${liveConnected ? "is-on" : "is-off"}`}
          role="status"
        >
          <span className="dot" aria-hidden="true" />
          {liveConnected ? "Live events connected" : "Reconnecting live events…"}
        </span>
      )}
    </header>
  );
}

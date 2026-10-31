import type { Via } from "../../types.ts";

const SOURCES: Record<Via, { label: string; agent: boolean }> = {
  claude: { label: "Agent", agent: true },
  merchant: { label: "You", agent: false },
  website: { label: "Website", agent: false },
};

/** Who made a booking: a dot (brick for an agent) and "Agent", "You" or "Website". */
export function Source({ via }: { via: Via }) {
  const { label, agent } = SOURCES[via];
  return (
    <span className={agent ? "src a" : "src"}>
      <i />
      {label}
    </span>
  );
}

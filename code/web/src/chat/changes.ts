import type { ChatEvent } from "../types.ts";

// The assistant's tools that change the merchant's data (#152). A tool's card comes only when
// it succeeded; the reads (`list_reservations`, `get_stats`, `try_request`, `show_view`) change
// nothing.
const CHANGING = new Set([
  "update_look",
  "update_rules",
  "update_policy",
  "update_tables",
  "block_slot",
  "unblock_slot",
  "cancel_reservation",
  "send_test_booking",
]);

/**
 * Follows one turn's events and says, once per tool, when one shows a change of the merchant's
 * data: a changing tool's card, or a publish's `deploy` done (`publish_agent`, or the top bar's
 * Publish, which runs no tool).
 */
export function changeWatcher(): (event: ChatEvent) => boolean {
  let tool: string | null = null;
  return (event) => {
    if (event.type === "tool_start") tool = event.tool;
    else if (event.type === "step") return event.step === "deploy" && event.status === "done";
    else if (event.type === "card" && tool !== null && CHANGING.has(tool)) {
      tool = null;
      return true;
    }
    return false;
  };
}

// The Agent page's routes (#136, #146, #151): the merchant, its public site, a test frame,
// publish, pause and resume, the node's request log; and the assistant's opening (#140).
import type { ManagementClient } from "../../management/client.ts";
import type { AgentIntent, AgentTryOutput, MerchantOverview } from "../../types.ts";
import { type Mock, invalid, log, respond } from "./context.ts";

const REQUESTS_LIMIT = 10;

type AgentRoutes = Pick<
  ManagementClient,
  | "merchant"
  | "assistant"
  | "site"
  | "tryAgent"
  | "publish"
  | "pauseAgent"
  | "resumeAgent"
  | "agentRequests"
>;

export function agentRoutes(mock: Mock): AgentRoutes {
  return {
    merchant: () => respond(mock, overview(mock)),
    assistant: () => respond(mock, { session_id: "mock-owner", items: [...mock.conversation] }),
    site: () => respond(mock, { pages: [...mock.sitePages] }),
    tryAgent: async (_slug, intent) => respond(mock, tryAgent(mock, intent)),

    async publish() {
      if (mock.data.merchant.status === "down") {
        invalid("Your agent is paused: resume it rather than publish it again.");
      }
      setStatus(mock, "live");
      return respond(mock, overview(mock));
    },

    async pauseAgent() {
      setStatus(mock, "down");
      log(mock, "paused", "Guests get your booking page.", "merchant");
      return respond(mock, { status: mock.data.merchant.status });
    },

    async resumeAgent() {
      // A merchant still building goes live by publishing it (#136), not here.
      if (mock.data.merchant.status === "building") invalid("Publish your agent first.");
      setStatus(mock, "live");
      log(mock, "resumed", "Your agent takes bookings again.", "merchant");
      return respond(mock, { status: mock.data.merchant.status });
    },

    agentRequests: (_slug, { date, refused }) =>
      respond(mock, {
        requests: mock.data.agent_requests
          .filter((r) => r.at.slice(0, 10) === date && (!refused || r.refused))
          .slice(0, REQUESTS_LIMIT),
      }),
  };
}

function overview(mock: Mock): MerchantOverview {
  return { ...mock.data.merchant, now: mock.nowIso() };
}

function setStatus(mock: Mock, status: MerchantOverview["status"]): void {
  mock.data = { ...mock.data, merchant: { ...mock.data.merchant, status } };
}

// Like the node: hidden until published, and a paused agent sends guests to the booking page.
function tryAgent(mock: Mock, intent: AgentIntent): AgentTryOutput {
  const anyMerchant = intent === "PING" || intent === "DISCOVER" || intent === "RECALL";
  const refused = anyMerchant ? null : { building: 404, down: 503, live: null }[mock.data.merchant.status];
  const status = refused ?? 200;
  mock.tries += 1;
  const id = `r-${mock.tries}`;
  const merchant = anyMerchant ? undefined : mock.data.merchant.slug;
  const answer =
    status === 200
      ? { result: {} }
      : {
          error:
            status === 404
              ? { code: "not_found", message: "Unknown merchant" }
              : { code: "agent_unavailable", message: "Not taking bookings through agents right now" },
        };
  return {
    request: JSON.stringify({ type: "request", id, intent, merchant, params: {} }),
    response: JSON.stringify({ type: "response", id, status, ...answer }),
    status,
  };
}

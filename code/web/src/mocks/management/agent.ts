// The Agent page's routes (#136, #146, #151): the merchant, its public site, a test frame,
// publish, pause and resume, the node's request log; and the assistant's opening (#140).
import type { ManagementClient } from "../../management/client.ts";
import type { AgentIntent, AgentTalkInput, AgentTryOutput, MerchantOverview, TalkOutput } from "../../types.ts";
import { type Mock, invalid, log, respond } from "./context.ts";

const REQUESTS_LIMIT = 10;

type AgentRoutes = Pick<
  ManagementClient,
  | "merchant"
  | "assistant"
  | "site"
  | "tryAgent"
  | "talkToAgent"
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
    talkToAgent: async (_slug, input) => respond(mock, talkToAgent(mock, input)),

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

// Like the node: the Send buttons' intents address no merchant, so whether it is published or
// paused changes nothing; booking is a TALK (#254).
function tryAgent(mock: Mock, intent: AgentIntent): AgentTryOutput {
  mock.tries += 1;
  const id = `r-${mock.tries}`;
  return {
    request: JSON.stringify({ type: "request", id, intent, params: {} }),
    response: JSON.stringify({ type: "response", id, status: 200, result: {} }),
    status: 200,
  };
}

// A scripted agent, with the node's refusals: a request for a table proposes one, a confirm books.
function talkToAgent(mock: Mock, input: AgentTalkInput): AgentTryOutput {
  const refused = { building: 404, down: 503, live: null }[mock.data.merchant.status];
  mock.tries += 1;
  const id = `r-${mock.tries}`;
  const params = { ...input, idempotency_key: `studio-try-${mock.tries}` };
  const request = JSON.stringify({ type: "request", id, intent: "TALK", merchant: mock.data.merchant.slug, params });
  if (refused) {
    const error =
      refused === 404
        ? { code: "not_found", message: "Unknown merchant" }
        : { code: "agent_unavailable", message: "Not taking bookings through agents right now" };
    return { request, response: JSON.stringify({ type: "response", id, status: refused, error }), status: refused };
  }
  const talk = scriptedAnswer(mock, input);
  return {
    request,
    response: JSON.stringify({ type: "response", id, status: 200, result: talk }),
    status: 200,
    talk,
  };
}

function scriptedAnswer(mock: Mock, input: AgentTalkInput): TalkOutput {
  const answer = {
    conversation: input.conversation ?? `c-${mock.tries}`,
    slots: [],
    pending: null,
    reservation: null,
    fallback_url: null,
  };
  if (input.confirm) {
    return {
      ...answer,
      reply: "It's booked. See you tonight.",
      reservation: {
        id: "R-900",
        starts_at: mock.nowIso(),
        party_size: 2,
        table_label: "T1",
        status: "confirmed",
        guest_name: "Test booking",
      },
      tools_called: ["create_reservation"],
    };
  }
  if (/table|book|reserv/i.test(input.message)) {
    return {
      ...answer,
      reply: "I can do that. Shall I book it?",
      pending: {
        id: `p-${mock.tries}`,
        action: "book",
        summary: "Book a table for 2 tonight, under the name Test booking.",
        expires_at: mock.nowIso(),
      },
      tools_called: ["get_availability", "propose_booking"],
    };
  }
  return { ...answer, reply: "Yes: seasonal bowls, all plant-based, at lunch and dinner.", tools_called: [] };
}

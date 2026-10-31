// What Studio shows for each conversation state (#141), with Node's own runner (`npm test`).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type ChatAction,
  type ChatState,
  chatReducer,
  initialChatState,
} from "../src/chat/state.ts";
import onboarding from "../src/mocks/onboarding.json" with { type: "json" };
import {
  SKIP,
  buildMessage,
  chipsOf,
  describesItself,
  feedOf,
  fitOf,
  initials,
  isBuilt,
  isEmpty,
  lastSeating,
  parseAddress,
  phaseOf,
  pillOf,
  siteName,
  stepNote,
} from "../src/studio/phase.ts";
import { ChatError } from "../src/chat/client.ts";
import { ManagementRequestError } from "../src/management/client.ts";
import { publishing } from "../src/studio/publish.ts";
import type { ChatEvent, MerchantOverview } from "../src/types.ts";

const BUILD = onboarding as ChatEvent[];
const HOST = "lagraineverte.example";

function run(state: ChatState, ...actions: ChatAction[]): ChatState {
  return actions.reduce(chatReducer, state);
}

const start = () => initialChatState("", null);
const chat = (event: ChatEvent): ChatAction => ({ type: "chat", event });

function asked(): ChatState {
  return run(start(), {
    type: "details",
    text: HOST,
    address: HOST,
    site: HOST,
    reply: `I can read **${HOST}**.`,
  });
}

function building(): ChatState {
  return run(
    asked(),
    { type: "send", text: "24 seats" },
    ...BUILD.slice(0, 3).map(chat),
  );
}

function reviewing(): ChatState {
  return run(asked(), { type: "send", text: "24 seats" }, ...BUILD.map(chat));
}

function published(): ChatState {
  return run(
    reviewing(),
    { type: "send", text: "Publish" },
    chat({ type: "step", step: "deploy", status: "running" }),
    chat({ type: "step", step: "deploy", status: "done" }),
    chat({ type: "done" }),
  );
}

test("address: nothing said yet", () => {
  const state = start();
  assert.equal(phaseOf(state), "address");
  assert.equal(isEmpty(state), true);
  assert.equal(pillOf(state), null);
  assert.deepEqual(feedOf(state), []);
});

test("describe: the address alone, answered with no model call", () => {
  const state = asked();
  assert.equal(phaseOf(state), "describe");
  assert.equal(state.busy, false);
  assert.equal(isEmpty(state), false);
  assert.equal(siteName(state), HOST);
  assert.deepEqual(pillOf(state), { label: "Not built yet", live: false });
  assert.deepEqual(feedOf(state).map((entry) => entry.kind), ["user", "bot"]);
  assert.deepEqual(chipsOf(state, "describe", []), [SKIP]);
});

test("an address with three other words goes straight to building", () => {
  const found = parseAddress(`https://www.${HOST}/, 24 seats, groups up to 4`);
  assert.equal(found?.host, HOST);
  assert.equal(found && describesItself(found), true);
  assert.equal(describesItself(parseAddress(`${HOST} 24 seats`)!), false);
  assert.equal(parseAddress("hello there"), null);
  assert.equal(parseAddress("localhost:8081")?.host, "localhost:8081");
  assert.equal(parseAddress(`look at ${HOST}.`)?.address, HOST);
});

test("an e-mail or a file's name is not an address", () => {
  assert.equal(parseAddress("contact@resto.fr"), null);
  assert.equal(parseAddress("Our menu.pdf is online, 40 seats"), null);
  assert.equal(parseAddress(`Our menu.pdf is on ${HOST}`)?.host, HOST);
  assert.equal(parseAddress(`https://${HOST}/menu.pdf`)?.host, HOST);
});

test("the assistant gets the address and the answer together", () => {
  assert.equal(buildMessage(HOST, "24 seats"), `${HOST}. 24 seats`);
  assert.equal(buildMessage(HOST, SKIP), `${HOST}. No description: use the defaults.`);
});

test("building: real step events drive the card, and the pill follows provision", () => {
  const state = building();
  assert.equal(phaseOf(state), "building");
  assert.deepEqual(pillOf(state), { label: "Not built yet", live: false });
  assert.equal(isBuilt(state), false);
  const entry = feedOf(state).find((item) => item.kind === "steps");
  assert.equal(entry?.kind === "steps" && entry.title, `Building ${HOST}`);
  assert.deepEqual(chipsOf(state, "building", []), []);

  const provisioned = run(
    state,
    chat({ type: "step", step: "provision", status: "done", detail: "9 tables, 24 seats." }),
  );
  assert.deepEqual(pillOf(provisioned), { label: "Hidden from guests", live: false });
  assert.equal(phaseOf(provisioned), "building");
});

test("review: the model's text gives way to the fixed sentences", () => {
  const state = reviewing();
  assert.equal(phaseOf(state), "review");
  assert.equal(siteName(state), "La Graine Verte");
  const entries = feedOf(state);
  assert.deepEqual(entries.map((entry) => entry.kind), ["user", "bot", "user", "steps", "review"]);
  const steps = entries[3];
  assert.equal(steps.kind === "steps" && steps.title, "Building La Graine Verte");
  // The mockup's eight details, the look (#149) included.
  assert.equal(steps.kind === "steps" && steps.found, 8);
  const review = entries[4];
  assert.equal(review.kind === "review" && review.pages, 1);
  assert.match((review.kind === "review" && review.loginUrl) || "", /token=/);
});

test("review: chips are computed from the profile, and only the ones not asked yet", () => {
  const state = reviewing();
  const chips = chipsOf(state, "review", []);
  assert.deepEqual(chips, [
    "Block 20:30 tonight",
    "Set max party size to 6",
    "What bookings do I have tonight?",
    "Publish",
  ]);
  const later = run(state, { type: "send", text: chips[0] }, chat({ type: "done" }));
  assert.equal(chipsOf(later, "review", []).includes(chips[0]), false);
});

test("review: asking for a change stays in review", () => {
  const state = run(reviewing(), { type: "send", text: "Block 20:30 tonight" });
  assert.equal(state.busy, true);
  assert.equal(phaseOf(state), "review");
});

test("live: publishing updates the build's card and adds the addresses", () => {
  const state = published();
  assert.equal(phaseOf(state), "live");
  assert.deepEqual(pillOf(state), { label: "Live", live: true });
  const entries = feedOf(state);
  const kinds = entries.map((entry) => entry.kind);
  assert.deepEqual(kinds, ["user", "bot", "user", "steps", "review", "user", "live"]);
  const steps = entries[3];
  assert.equal(steps.kind === "steps" && steps.steps.deploy?.status, "done");
  assert.deepEqual(chipsOf(state, "live", []), [
    "What bookings do I have tonight?",
    "How busy are we tonight?",
  ]);
});

test("live, by chat: the model's words and the confirmation give way too", () => {
  const state = run(
    reviewing(),
    { type: "send", text: "Publish it" },
    chat({ type: "step", step: "deploy", status: "running" }),
    chat({ type: "step", step: "deploy", status: "done", detail: "Your agent is live." }),
    chat({ type: "card", kind: "confirmation", data: { text: "Your agent is live." } }),
    chat({ type: "text", delta: "Your agent is live: guests can book you." }),
    chat({ type: "done" }),
  );
  assert.equal(phaseOf(state), "live");
  assert.deepEqual(feedOf(state).slice(-2).map((entry) => entry.kind), ["user", "live"]);
});

test("a failed publish stays hidden, and says so", () => {
  const thrown = run(
    reviewing(),
    { type: "send", text: "Publish" },
    chat({ type: "step", step: "deploy", status: "running" }),
    { type: "failed", message: "I can't reach the platform right now." },
  );
  assert.equal(phaseOf(thrown), "review");
  assert.deepEqual(pillOf(thrown), { label: "Hidden from guests", live: false });
  assert.equal(feedOf(thrown).at(-1)?.kind, "error");

  const refused = run(
    reviewing(),
    { type: "send", text: "Publish" },
    chat({ type: "step", step: "deploy", status: "running" }),
    chat({ type: "step", step: "deploy", status: "error", detail: "It didn't work." }),
    chat({ type: "done" }),
  );
  assert.equal(phaseOf(refused), "review");
  assert.equal(feedOf(refused).at(-1)?.kind, "error");
});

async function publishEvents(publish: () => Promise<MerchantOverview>): Promise<ChatEvent[]> {
  const fromChat: ChatEvent[] = [{ type: "text", delta: "From the assistant." }, { type: "done" }];
  const chat = {
    async *send() {
      yield* fromChat;
    },
  };
  const events: ChatEvent[] = [];
  for await (const event of publishing({ publish }, chat, "la-graine-verte", "s", new AbortController().signal)) {
    events.push(event);
  }
  return events;
}

test("Publish goes through the route, and through the assistant without the cookie", async () => {
  const done = await publishEvents(async () => ({}) as MerchantOverview);
  assert.deepEqual(done.map((event) => event.type), ["step", "step", "done"]);

  const unsigned = await publishEvents(async () => {
    throw new ManagementRequestError(401, { error: "not_signed_in", message: "Open your private link." });
  });
  assert.deepEqual(unsigned.map((event) => event.type), ["step", "text", "done"]);

  await assert.rejects(
    publishEvents(async () => {
      throw new ManagementRequestError(0, { error: "agent_unavailable", message: "I can't reach the platform right now." });
    }),
    (error: Error) => error instanceof ChatError && /can't reach/.test(error.message),
  );
});

test("a failed build shows the model's explanation and goes back to the address", () => {
  const state = run(
    asked(),
    { type: "send", text: "24 seats" },
    chat({ type: "step", step: "crawl", status: "running" }),
    chat({ type: "step", step: "crawl", status: "error", detail: "I can't open it." }),
    chat({ type: "text", delta: "Check the address and send it again." }),
    chat({ type: "done" }),
  );
  assert.equal(phaseOf(state), "address");
  assert.equal(feedOf(state).at(-1)?.kind, "bot");
});

test("a merchant found again after a reload starts from its real status", () => {
  const state = run(start(), {
    type: "attached",
    merchant: { slug: "la-graine-verte", name: "La Graine Verte" },
    welcome: "Welcome back!",
  });
  assert.equal(isEmpty(state), false);
  assert.deepEqual(feedOf(state).map((entry) => entry.kind), ["bot"]);
  const hidden = run(state, { type: "status", status: "building" });
  assert.equal(phaseOf(hidden), "review");
  assert.deepEqual(pillOf(hidden), { label: "Hidden from guests", live: false });
  assert.equal(phaseOf(run(state, { type: "status", status: "live" })), "live");
});

test("the pages the crawl read arrive during the build, one entry per path", () => {
  const page = { path: "/", width: 1000, height: 900, image: "data:image/jpeg;base64,AA==" };
  const other = { ...page, path: "/horaires.html" };
  const state = run(
    building(),
    chat({ type: "site_page", page }),
    chat({ type: "site_page", page: other }),
    chat({ type: "site_page", page: { ...page, height: 800 } }),
  );
  assert.deepEqual(state.sitePages.map((item) => [item.path, item.height]), [["/horaires.html", 900], ["/", 800]]);
  assert.equal(phaseOf(state), "building");
  assert.deepEqual(run(state, { type: "reset", welcome: "" }).sitePages, []);
});

test("frames are 1000 wide and scaled to fit; Both stands side by side from 1240", () => {
  const room = (width: number) => ({ width, height: 600 });
  assert.deepEqual(fitOf("manage", room(500), false), { width: 1000, scale: 0.5, across: false, stacked: false });
  assert.deepEqual(fitOf("manage", room(1400), false), { width: 1000, scale: 1, across: false, stacked: false });
  assert.deepEqual(fitOf("both", room(700), false), { width: 1000, scale: 0.7, across: false, stacked: true });
  const across = fitOf("both", room(1240), false);
  assert.equal(across.width, 760);
  assert.equal(across.across, true);
  assert.equal(across.scale, (1240 - 24) / 2 / 760);
  // On a phone, the frame takes the room there is, and Both isn't offered.
  assert.deepEqual(fitOf("both", room(358), true), { width: 358, scale: 1, across: false, stacked: false });
});

test("a new conversation forgets everything", () => {
  const state = run(reviewing(), { type: "reset", welcome: "" });
  assert.equal(phaseOf(state), "address");
  assert.equal(isEmpty(state), true);
  assert.equal(state.merchant, null);
  assert.equal(state.site, null);
});

test("the extract note counts what the review card found", () => {
  const done = { status: "done" } as const;
  assert.equal(stepNote("extract", done, 7), "Found 7 details.");
  assert.equal(stepNote("extract", done, 1), "Found 1 detail.");
  assert.equal(stepNote("extract", done, null), null);
  assert.equal(stepNote("deploy", done, null), "Agents get real answers now.");
  assert.equal(stepNote("deploy", { status: "waiting", detail: "Waiting for you." }, 7), "Waiting for you.");
  assert.equal(stepNote("validate", { status: "done", detail: "Valid." }, 7), "Valid.");
});

test("the last seating is an hour before the end, on the half hour", () => {
  assert.equal(lastSeating([{ start: "11:30", end: "14:30" }, { start: "18:30", end: "21:30" }]), "20:30");
  assert.equal(lastSeating([{ start: "19:00", end: "21:45" }]), "20:30");
  assert.equal(lastSeating([{ start: "19:00", end: "19:30" }]), null);
  assert.equal(lastSeating([]), null);
});

test("tiles take two letters, without the articles", () => {
  assert.equal(initials("La Graine Verte"), "GV");
  assert.equal(initials("Le Petit Zinc"), "PZ");
  assert.equal(initials("Maison Albane"), "MA");
  assert.equal(initials("Noma"), "No");
});

test("a change shows as a diff card, and a view card isn't a message (#152)", () => {
  const state = run(
    reviewing(),
    { type: "send", text: "Change the main colour to #2450c8" },
    chat({
      type: "card",
      kind: "diff",
      data: { label: "Main colour", before: "#727f55", after: "#2450c8", view: "manage" },
    }),
    chat({ type: "card", kind: "view", data: { view: "agent" } }),
    chat({ type: "done" }),
  );

  const kinds = feedOf(state).map((entry) => entry.kind);
  assert.equal(kinds.filter((kind) => kind === "card").length, 1);
  const card = feedOf(state).findLast((entry) => entry.kind === "card");
  assert.ok(card?.kind === "card" && card.card.kind === "diff");
});

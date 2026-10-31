// The chat reducer with Node's own runner (`npm test`): a build cut short leaves no
// spinner and no "Building" pill (#86).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ChatEvent } from "../src/types.ts";
import {
  type ChatAction,
  type ChatState,
  type StepGroup,
  chatReducer,
  initialChatState,
} from "../src/chat/state.ts";

const ZINC = { slug: "le-petit-zinc", name: "Le Petit Zinc" };

function run(state: ChatState, ...actions: ChatAction[]): ChatState {
  return actions.reduce(chatReducer, state);
}

// Onboarding up to `extract` running, as when the stream drops mid-build.
function midBuild(merchant: typeof ZINC | null = null): ChatState {
  return run(
    initialChatState("Hello", merchant),
    { type: "send", text: "http://zinc.example/" },
    { type: "chat", event: { type: "tool_start", tool: "build_agent", label: "Building" } },
    { type: "chat", event: { type: "step", step: "crawl", status: "done", duration_ms: 900 } },
    { type: "chat", event: { type: "step", step: "extract", status: "running" } },
  );
}

function steps(state: ChatState): StepGroup["steps"] {
  const group = state.items.findLast((item) => item.type === "steps");
  assert.ok(group?.type === "steps");
  return group.steps;
}

test("a stream cut mid-build stops the spinner and the Building pill", () => {
  const state = midBuild();
  assert.equal(state.status, "building");

  const cut = chatReducer(state, { type: "failed", message: "The connection was interrupted." });

  assert.equal(steps(cut).crawl?.status, "done");
  assert.deepEqual(steps(cut).extract, { status: "error", detail: "Interrupted" });
  assert.equal(cut.status, null);
  assert.equal(cut.busy, false);
  assert.equal(cut.items.at(-1)?.type, "error");
});

test("a rebuild cut short leaves an onboarded merchant live", () => {
  const cut = chatReducer(midBuild(ZINC), { type: "failed", message: "Interrupted" });

  assert.equal(cut.status, "live");
});

test("a turn that ends with a step still running stops it too", () => {
  const ended = chatReducer(midBuild(), { type: "chat", event: { type: "done" } });

  assert.equal(steps(ended).extract?.status, "error");
  assert.equal(ended.status, null);
});

test("a finished build is left as it is", () => {
  const built = run(
    midBuild(),
    ...(["extract", "validate", "provision", "deploy"] as const).map(
      (step): ChatAction => ({
        type: "chat",
        event: { type: "step", step, status: "done", duration_ms: 100 },
      }),
    ),
    { type: "chat", event: { type: "done" } },
  );

  assert.ok(Object.values(steps(built)).every((step) => step?.status === "done"));
  assert.equal(built.status, "live");
});

test("a failure outside a build changes no status", () => {
  const live = run(initialChatState("Hello", ZINC), { type: "send", text: "Tonight?" });

  const cut = chatReducer(live, { type: "failed", message: "Interrupted" });

  assert.equal(cut.status, "live");
  assert.deepEqual(
    cut.items.map((item) => item.type),
    ["assistant", "user", "error"],
  );
});

test("a failed first build shows the same pill as a cut one: none", () => {
  const failed = chatReducer(midBuild(), {
    type: "chat",
    event: { type: "step", step: "extract", status: "error", detail: "No profile" },
  });

  assert.equal(failed.status, null);
  assert.equal(chatReducer(midBuild(ZINC), {
    type: "chat",
    event: { type: "step", step: "extract", status: "error" },
  }).status, "live");
});

test("a built agent's card leaves the pill on building until it is published (#136)", async () => {
  const onboarding = (await import("../src/mocks/onboarding.json", { with: { type: "json" } }))
    .default as ChatEvent[];
  const built = onboarding.reduce(
    (state, event) => chatReducer(state, { type: "chat", event }),
    chatReducer(initialChatState("Hello", null), { type: "send", text: "http://zinc.example/" }),
  );

  assert.equal(built.status, "building");
  assert.equal(built.merchant?.slug, "la-graine-verte");
  assert.equal(steps(built).deploy?.status, "waiting");

  const published = run(
    built,
    { type: "send", text: "Publish it" },
    { type: "chat", event: { type: "step", step: "deploy", status: "running" } },
    { type: "chat", event: { type: "step", step: "deploy", status: "done" } },
  );
  assert.equal(published.status, "live");
});

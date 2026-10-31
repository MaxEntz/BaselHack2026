// Which of a turn's events show that the assistant changed the merchant's data (#137, #152).
import assert from "node:assert/strict";
import { test } from "node:test";

import { changeWatcher } from "../src/chat/changes.ts";
import type { ChatEvent } from "../src/types.ts";

const CONFIRMATION: ChatEvent = { type: "card", kind: "confirmation", data: { text: "Done." } };
const DIFF: ChatEvent = {
  type: "card",
  kind: "diff",
  data: { label: "Corners", before: "organic", after: "square", view: "manage" },
};

function start(tool: string): ChatEvent {
  return { type: "tool_start", tool, label: tool };
}

function changes(...events: ChatEvent[]): boolean[] {
  return events.map(changeWatcher());
}

test("a changing tool's card is a change", () => {
  assert.deepEqual(changes(start("block_slot"), CONFIRMATION, { type: "done" }), [false, true, false]);
});

test("a read's card is not, even a confirmation", () => {
  assert.deepEqual(changes(start("try_request"), CONFIRMATION), [false, false]);
  assert.deepEqual(changes(start("show_view"), { type: "card", kind: "view", data: { view: "manage" } }), [
    false,
    false,
  ]);
});

test("a changing tool that failed shows no card, so no change", () => {
  assert.deepEqual(changes(start("update_policy"), { type: "text", delta: "That failed." }), [false, false]);
});

test("a tool with several cards changes once", () => {
  assert.deepEqual(changes(start("update_look"), DIFF, DIFF), [false, true, false]);
});

test("a publish is a change when its deploy is done", () => {
  assert.deepEqual(
    changes(
      { type: "step", step: "deploy", status: "running" },
      { type: "step", step: "deploy", status: "done" },
      { type: "step", step: "deploy", status: "waiting" },
    ),
    [false, true, false],
  );
});

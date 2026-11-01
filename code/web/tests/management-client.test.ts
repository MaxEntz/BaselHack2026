// The management site's HTTP client (#129) against answers it can't trust (#220): a body that
// isn't an object is the platform failing to answer, never a value of the wrong shape.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ManagementRequestError, httpManagementClient } from "../src/management/client.ts";

const SLUG = "la-graine-verte";

function answer(status: number, body: string) {
  globalThis.fetch = async () => new Response(body, { status });
}

function refused(thrown: unknown): ManagementRequestError {
  assert.ok(thrown instanceof ManagementRequestError);
  return thrown;
}

test("a success that isn't a JSON object is no answer", async () => {
  for (const body of ["null", '"ok"', "42", "not json"]) {
    answer(200, body);
    await assert.rejects(httpManagementClient.hours(SLUG), (thrown: unknown) => {
      const error = refused(thrown);
      assert.equal(error.status, 200);
      assert.equal(error.body.error, "agent_unavailable");
      assert.match(error.body.message, /couldn't answer/);
      return true;
    }, body);
  }
});

test("an object answer goes through", async () => {
  answer(200, '{"services": []}');
  assert.deepEqual(await httpManagementClient.hours(SLUG), { services: [] });
});

test("an error body keeps its fields when they have the right type", async () => {
  answer(
    422,
    '{"error": "invalid_input", "message": "Check the hours", "problem": {"code": "overlap", "services": [0, 1]}}',
  );
  await assert.rejects(httpManagementClient.hours(SLUG), (thrown: unknown) => {
    assert.deepEqual(refused(thrown).body, {
      error: "invalid_input",
      message: "Check the hours",
      problem: { code: "overlap", services: [0, 1] },
    });
    return true;
  });
});

test("an error body with a field of the wrong type loses it", async () => {
  answer(422, '{"error": 4, "message": "Nope"}');
  await assert.rejects(httpManagementClient.hours(SLUG), (thrown: unknown) => {
    assert.deepEqual(refused(thrown).body, { error: "agent_unavailable", message: "The platform couldn't answer." });
    return true;
  });

  answer(422, '{"error": "invalid_input", "message": "Nope", "problem": "overlap", "fallback_url": 3}');
  await assert.rejects(httpManagementClient.hours(SLUG), (thrown: unknown) => {
    assert.deepEqual(refused(thrown).body, { error: "invalid_input", message: "Nope" });
    return true;
  });
});

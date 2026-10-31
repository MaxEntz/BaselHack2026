// The chat client's idle timeout against the platform's heartbeats (#87): a turn that only
// receives `: ping` comments is alive, a turn that receives nothing is not.
import assert from "node:assert/strict";
import { mock, test } from "node:test";

import { readSse } from "../src/chat/sse.ts";

// `client.ts` schedules its timers on `window`: Node's globals stand in for it.
Object.assign(globalThis, { window: globalThis });
const { ChatError, httpClient } = await import("../src/chat/client.ts");

const encoder = new TextEncoder();
const PING = ": ping - 2026-10-31 12:00:00\r\n\r\n";
const DONE = 'event: done\r\ndata: {"type": "done"}\r\n\r\n';

// What `fetch` returns: a stream the test writes to, failing when the request is aborted.
function fakeFetch(): { write: (text: string) => void } {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  globalThis.fetch = async (_url, init) => {
    init?.signal?.addEventListener("abort", () => controller.error(init.signal?.reason));
    return new Response(body, { headers: { "content-type": "text/event-stream" } });
  };
  return { write: (text) => controller.enqueue(encoder.encode(text)) };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

async function turn(script: (write: (text: string) => void) => Promise<void>) {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { write } = fakeFetch();
    const events: string[] = [];
    const result = (async () => {
      for await (const event of httpClient.send("hi", "s", new AbortController().signal)) {
        events.push(event.type);
      }
    })().then(
      () => null,
      (error: unknown) => error,
    );
    await settle();
    await script(write);
    return { error: await result, events };
  } finally {
    mock.timers.reset();
  }
}

async function wait(ms: number) {
  mock.timers.tick(ms);
  await settle();
}

test("a turn kept alive by heartbeats outlasts the 90 s idle timeout", async () => {
  const { error, events } = await turn(async (write) => {
    // 105 s of heartbeats only, as during a slow LLM call, then the answer.
    for (let second = 0; second < 105; second += 15) {
      write(PING);
      await settle();
      await wait(15_000);
    }
    write(DONE);
    await settle();
  });

  assert.equal(error, null);
  assert.deepEqual(events, ["done"]);
});

test("a silent turn still gives up after 90 s", async () => {
  const { error } = await turn(async () => {
    await wait(90_000);
  });

  assert.ok(error instanceof ChatError);
  assert.match(error.message, /interrupted/);
});

test("readSse reports every chunk, comments included", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(PING));
      controller.enqueue(encoder.encode(DONE));
      controller.close();
    },
  });
  let chunks = 0;

  const frames = [];
  for await (const frame of readSse(body, () => chunks++)) frames.push(frame);

  assert.equal(chunks, 2);
  assert.deepEqual(frames, [{ event: "done", data: '{"type": "done"}' }]);
});

test("a refused build asks for the invitation link (#132)", async () => {
  globalThis.fetch = async () => new Response('{"error":"needs_invite"}', { status: 403 });
  const { NEEDS_INVITE } = await import("../src/chat/client.ts");

  const turn = httpClient.send("hi", "s", new AbortController().signal).next();

  await assert.rejects(turn, (error: unknown) => {
    assert.ok(error instanceof ChatError);
    assert.equal(error.message, NEEDS_INVITE);
    return true;
  });
});

test("an invitation code is sent once, as JSON, with the cookie", async () => {
  const sent: RequestInit[] = [];
  globalThis.fetch = async (_url, init) => {
    sent.push(init ?? {});
    return new Response(null, { status: 204 });
  };
  const { acceptInvite } = await import("../src/chat/client.ts");

  assert.equal(await acceptInvite("olive-2026"), true);
  assert.deepEqual(
    sent.map((init) => [init.method, init.body, init.credentials]),
    [["POST", '{"code":"olive-2026"}', "same-origin"]],
  );
  globalThis.fetch = async () => new Response(null, { status: 403 });
  assert.equal(await acceptInvite("wrong"), false);
});

test("after a build the session claims its owner cookie", async () => {
  const sent: [string, RequestInit][] = [];
  globalThis.fetch = async (url, init) => {
    sent.push([String(url), init ?? {}]);
    return new Response(null, { status: 204 });
  };

  await httpClient.claim("builder");

  assert.deepEqual(
    sent.map(([url, init]) => [url, init.method, init.body]),
    [["/api/session/claim", "POST", '{"session_id":"builder"}']],
  );
});

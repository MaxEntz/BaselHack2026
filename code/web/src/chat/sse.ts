export type SseFrame = { event: string; data: string };

// Splits a text/event-stream body into frames (event: / data: / blank line).
export async function* readSse(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // A trailing "\r" may be the first half of a "\r\n" split across chunks.
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(
        /\r\n|\r(?!$)/g,
        "\n",
      );
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const frame = parseFrame(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (frame) yield frame;
        end = buffer.indexOf("\n\n");
      }
    }
    const frame = parseFrame(buffer.replace(/\r$/, ""));
    if (frame) yield frame;
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(block: string): SseFrame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    // Lines starting with ":" are comments (heartbeats).
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length > 0 ? { event, data: data.join("\n") } : null;
}

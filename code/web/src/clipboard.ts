/** A copy's outcome, and what to tell the person. */
export type Copy = { copied: boolean; message: string };

/**
 * Copies `text`. The clipboard needs a secure context (HTTPS, localhost), which a projector on a
 * LAN address may not be: the message then shows the text, to copy by hand.
 */
export async function copyText(text: string): Promise<Copy> {
  try {
    await navigator.clipboard.writeText(text);
    return { copied: true, message: "Copied" };
  } catch {
    return { copied: false, message: `Copy this: ${text}` };
  }
}

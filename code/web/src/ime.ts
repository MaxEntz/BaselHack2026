import type { KeyboardEvent } from "react";

/**
 * True while an input method composes (Japanese, Chinese…): its Enter picks a word, it doesn't
 * send. Safari ends the composition before that keydown and reports it only as keyCode 229.
 */
export function composing(event: KeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229;
}

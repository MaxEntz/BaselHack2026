import { useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, Ref } from "react";
import { composing } from "../ime.ts";
import { FIRST_PLACEHOLDER, PLACEHOLDERS, type Phase } from "./phase.ts";

const MAX_INPUT = 150;

/**
 * What the page around the composer may do with it. The text is the composer's own state, so
 * that a keystroke re-renders the composer only, never the feed or the canvas (#220).
 */
export type ComposerHandle = {
  focus: (options?: FocusOptions) => void;
  setText: (text: string) => void;
};

type ComposerProps = {
  ref?: Ref<ComposerHandle>;
  chips: readonly string[];
  // The conversation hasn't started.
  empty: boolean;
  phase: Phase;
  busy: boolean;
  // The text typed; the page clears it through `setText` once it takes it.
  onSay: (text: string) => void;
  onChip: (chip: string) => void;
};

/** The chips and the message box: Enter sends, Shift+Enter and an IME's Enter don't. */
export function Composer({ ref, chips, empty, phase, busy, onSay, onChip }: ComposerProps) {
  const [input, setInput] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(
    ref,
    () => ({ focus: (options) => textarea.current?.focus(options), setText: setInput }),
    [],
  );

  // The box grows with its text, up to MAX_INPUT pixels.
  useLayoutEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_INPUT)}px`;
  }, [input, empty]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    onSay(input);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !composing(event)) {
      event.preventDefault();
      onSay(input);
    }
  };

  return (
    <form className="st-composer" onSubmit={onSubmit}>
      {chips.length > 0 && (
        <div className={chips.length <= 3 ? "st-chips wrap" : "st-chips"}>
          {chips.map((chip) => (
            <button key={chip} type="button" className="st-chip" onClick={() => onChip(chip)}>
              {chip}
            </button>
          ))}
        </div>
      )}
      <div className="st-box">
        <label className="st-sr" htmlFor="st-input">
          Message
        </label>
        <textarea
          id="st-input"
          ref={textarea}
          rows={1}
          value={input}
          placeholder={empty ? FIRST_PLACEHOLDER : PLACEHOLDERS[phase]}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" className="st-send" aria-label="Send" disabled={busy || !input.trim()}>
          <svg className="st-ico" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M6 11l6-6 6 6" />
          </svg>
        </button>
      </div>
    </form>
  );
}

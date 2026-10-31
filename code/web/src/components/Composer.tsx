import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

// The platform API rejects longer messages (#15).
const MAX_MESSAGE_LENGTH = 4000;

type Props = {
  disabled: boolean;
  onSend: (text: string) => void;
};

export function Composer({ disabled, onSend }: Props) {
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);

  // Give the focus back once the assistant has finished its turn.
  useEffect(() => {
    if (!disabled) input.current?.focus();
  }, [disabled]);

  function submit() {
    const message = text.trim();
    if (!message || disabled) return;
    onSend(message);
    setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter adds a new line; IME composition is left alone.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={input}
        value={text}
        rows={1}
        maxLength={MAX_MESSAGE_LENGTH}
        placeholder={disabled ? "The assistant is answering…" : "Write a message…"}
        aria-label="Message"
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  );
}

// jsdom has no <dialog> methods: a minimal stand-in with the events the Dialog listens to.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { setClock } from "./clock.ts";

afterEach(() => {
  cleanup();
  setClock(null);
});

const proto = HTMLDialogElement.prototype as HTMLDialogElement & { __polyfilled?: boolean };
if (typeof proto.showModal !== "function") {
  proto.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  proto.show = proto.showModal;
  proto.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
}

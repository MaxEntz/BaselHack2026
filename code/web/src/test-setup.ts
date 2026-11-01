// jsdom has no <dialog> methods: a minimal stand-in with the events the Dialog listens to.
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { setClock } from "./clock.ts";

// `findBy…` waits 1 s by default: a page's reads behind a loaded CPU (CI, the full suite) can
// take longer, and "Guests > shows a guest an agent just booked" failed so (#220).
configure({ asyncUtilTimeout: 5000 });

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

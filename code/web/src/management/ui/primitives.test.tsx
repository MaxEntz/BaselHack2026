import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Blob, Box, Dialog, Pill, SaveBar, TOAST_ACTION_MS, TOAST_MS, ToastProvider, blobNumber, useToast } from "./index.ts";

describe("Blob", () => {
  it("assigns b1-b4 by (index % 4) + 1", () => {
    expect([0, 1, 2, 3, 4, 5, 7].map(blobNumber)).toEqual([1, 2, 3, 4, 1, 2, 4]);
    const { container } = render(<Blob index={6} />);
    const svg = container.querySelector("svg")!;
    expect(svg.dataset.blob).toBe("b3");
    expect(svg.getAttribute("viewBox")).toBe("0 0 100 100");
    expect(svg.getAttribute("preserveAspectRatio")).toBe("none");
  });
});

describe("Pill and Box", () => {
  it("maps props to the mockup's classes", () => {
    render(
      <>
        <Pill primary small>
          Save
        </Pill>
        <Box tone="olive">Olive</Box>
        <Box tone="cream">Cream</Box>
      </>,
    );
    expect(screen.getByRole("button", { name: "Save" }).className).toBe("pill pri sm");
    expect(screen.getByText("Olive").className).toBe("box olive on-dark");
    expect(screen.getByText("Cream").className).toBe("box cream");
  });
});

function DialogHarness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true);
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/other")}>
        Go
      </button>
      <Dialog
        open={open}
        onClose={() => {
          onClose();
          setOpen(false);
        }}
        title="R-014"
      >
        <p>Body</p>
      </Dialog>
    </>
  );
}

function renderDialog() {
  const onClose = vi.fn();
  render(
    <MemoryRouter>
      <Routes>
        <Route path="*" element={<DialogHarness onClose={onClose} />} />
      </Routes>
    </MemoryRouter>,
  );
  return onClose;
}

describe("Dialog", () => {
  it("closes by its x", () => {
    const onClose = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Close", hidden: true }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByText("Body")).toBeNull();
  });

  it("closes on a click on the backdrop, not inside", () => {
    const onClose = renderDialog();
    fireEvent.click(screen.getByText("Body"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector("dialog")!);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on Esc (the dialog's close event)", () => {
    const onClose = renderDialog();
    act(() => document.querySelector("dialog")!.close());
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on a route change", () => {
    const onClose = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

function ToastButtons() {
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => toast("Plain")}>
        plain
      </button>
      <button type="button" onClick={() => toast("With action", { label: "See it", run: () => {} })}>
        action
      </button>
    </>
  );
}

describe("Toast", () => {
  afterEach(() => vi.useRealTimers());

  it("stays 3.6 s, or 9 s with an action", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <ToastButtons />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("plain"));
    fireEvent.click(screen.getByText("action"));
    act(() => vi.advanceTimersByTime(TOAST_MS - 1));
    expect(screen.getByText("Plain")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("Plain")).toBeNull();
    expect(screen.getByText("With action")).toBeTruthy();
    act(() => vi.advanceTimersByTime(TOAST_ACTION_MS - TOAST_MS));
    expect(screen.queryByText("With action")).toBeNull();
  });
});

describe("SaveBar", () => {
  it("is hidden until dirty, and an error replaces the label as an alert", () => {
    const { rerender } = render(<SaveBar dirty={false} onDiscard={() => {}} />);
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    rerender(<SaveBar dirty onDiscard={() => {}} />);
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<SaveBar dirty error="Keep at least one table." onDiscard={() => {}} />);
    expect(screen.getByRole("alert").textContent).toBe("Keep at least one table.");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });
});

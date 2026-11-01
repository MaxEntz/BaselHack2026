import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { BlockDialog } from "./BlockDialog.tsx";
import type { ManagementClient } from "./client.ts";
import { ClientContext } from "./data.ts";
import { ToastProvider } from "./ui/index.ts";

it("says what the server blocked, not what was ticked", async () => {
  // The server ignores a time that passed while the dialog was open (#133).
  const client = { setBlocked: async () => ({ blocked: [] }) } as unknown as ManagementClient;
  render(
    <MemoryRouter>
      <ClientContext.Provider value={client}>
        <ToastProvider>
          <BlockDialog
            slug="la-graine-verte"
            date="2026-10-31"
            services={[{ start: "18:30", end: "21:30" }]}
            blocked={[]}
            open
            onClose={() => {}}
          />
        </ToastProvider>
      </ClientContext.Provider>
    </MemoryRouter>,
  );

  fireEvent.click(screen.getByLabelText("20:00"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("No time is blocked on this day.")).toBeTruthy();
  expect(screen.queryByText(/Blocked 20:00/)).toBeNull();
});

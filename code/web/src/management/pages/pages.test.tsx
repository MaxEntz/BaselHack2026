import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import fixture from "../../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { createMockManagementClient } from "../../mocks/managementClient.ts";
import { routes } from "../../router.tsx";
import type { ManagementFixture } from "../../types.ts";
import { ClientContext } from "../data.ts";

const SLUG = "la-graine-verte";

function open(page: string, liveBookingDelayMs?: number) {
  const client = createMockManagementClient(fixture as ManagementFixture, {
    latencyMs: 0,
    liveBookingDelayMs,
  });
  const router = createMemoryRouter(routes, { initialEntries: [`/manage/${SLUG}/${page}`] });
  render(
    <ClientContext.Provider value={client}>
      <RouterProvider router={router} />
    </ClientContext.Provider>,
  );
  return { client, router };
}

// A field's label also holds its hint.
const field = (label: string) => screen.findByLabelText<HTMLInputElement>(new RegExp(`^${label}`));
const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const alert = () => screen.findByRole("alert");

describe("Opening hours", () => {
  it("shows the last table while typing, and Discard restores the saved hours", async () => {
    open("hours");
    const end = await field("Dinner ends");
    expect(screen.getByText("Last table 13:30")).toBeTruthy();
    expect(screen.queryByText("Unsaved changes")).toBeNull();

    type(end, "22:00");
    expect(screen.getByText("Last table 21:00")).toBeTruthy();
    expect(screen.getByText("Unsaved changes")).toBeTruthy();

    click("Discard");
    expect(end.value).toBe("21:30");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it.each([
    ["Dinner ends", "19:00", "Dinner has to last at least an hour."],
    ["Lunch ends", "", "Give Lunch a start and an end."],
    ["Lunch ends", "19:00", "Lunch and Dinner overlap."],
  ])("refuses %s at %j: %s", async (label, value, message) => {
    open("hours");
    type(await field(label), value);
    click("Save");
    expect((await alert()).textContent).toBe(message);
  });

  it("saves, then has nothing left to save", async () => {
    const { client } = open("hours");
    type(await field("Dinner starts"), "18:00");
    click("Save");

    expect((await screen.findByText("Saved")).parentElement?.textContent).toBe(
      "SavedClaude reads the new rules now.",
    );
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    expect((await client.hours(SLUG)).services[1]).toEqual({ start: "18:00", end: "21:30" });
  });

  it("opens a blocked time again", async () => {
    open("hours");
    fireEvent.click(await screen.findByRole("button", { name: "Sun 1 Nov, 13:00, open it again" }));

    expect(await screen.findByText("It's open again.")).toBeTruthy();
    expect(screen.getByText("Nothing is blocked.")).toBeTruthy();
  });

  it("blocks a time today", async () => {
    open("hours");
    fireEvent.click(await screen.findByRole("button", { name: "Block a time" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/^Saturday 31 October\. Agents stop offering/)).toBeTruthy();
    expect(within(dialog).getByRole("group", { name: "Lunch" })).toBeTruthy();

    fireEvent.click(within(dialog).getByLabelText("20:00"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect((await screen.findByText("Blocked 20:00")).parentElement?.textContent).toBe(
      "Blocked 20:00Agents stop offering it.",
    );
    expect(screen.getByRole("button", { name: "Sat 31 Oct, 20:00, open it again" })).toBeTruthy();
  });
});

describe("Guests", () => {
  const names = () =>
    screen
      .getAllByRole<HTMLTableRowElement>("row")
      .slice(1)
      .map((row) => row.cells[0].textContent);

  it("lists everyone booked ahead, by name, with their next visit", async () => {
    open("guests");
    const lena = (await screen.findByRole("cell", { name: "Lena Becker" })).closest("tr")!;

    expect([...lena.cells].map((cell) => cell.textContent)).toEqual([
      "Lena Becker",
      "Thu 5 Nov, 19:30",
      "4",
      "2",
      "Agent",
    ]);
    expect(names()).toEqual(names().toSorted((a, b) => a!.localeCompare(b!)));
  });

  it("finds a guest whatever the accents, case and spacing", async () => {
    open("guests");
    await screen.findByRole("cell", { name: "Lena Becker" });

    type(screen.getByPlaceholderText("Find a guest"), "  LÉNA  bec");
    await waitFor(() => expect(names()).toEqual(["Lena Becker"]));

    type(screen.getByPlaceholderText("Find a guest"), "Nobody");
    expect(await screen.findByText("Nobody by that name.")).toBeTruthy();
  });

  it("shows a guest an agent just booked", async () => {
    open("guests", 10);
    expect(await screen.findByRole("cell", { name: "Kim Weber" })).toBeTruthy();
  });
});

describe("Booking rules", () => {
  it("shows what an AI agent reads, and follows a save", async () => {
    open("rules");
    expect((await field("How long a table is held")).value).toBe("120 minutes");
    expect(screen.getByText(/Groups up to 4\.$/)).toBeTruthy();

    type(await field("Largest group"), "3");
    click("Save");

    expect(await screen.findByText(/Groups up to 3\.$/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "What Claude reads about you" })).toBeTruthy();
  });

  it.each([
    ["Largest group", "0", "The largest group has to be at least 1."],
    ["Your booking page", "https://elsewhere.example/", "Your booking page has to be on your own website."],
  ])("refuses %s %j: %s", async (label, value, message) => {
    open("rules");
    type(await field(label), value);
    click("Save");
    expect((await alert()).textContent).toBe(message);
  });
});

describe("Settings", () => {
  it("shows the website it reads, and needs a name", async () => {
    open("settings");
    expect((await field("Website")).value).toBe("localhost:8081");

    type(await field("Name"), "  ");
    click("Save");
    expect((await alert()).textContent).toBe("Your restaurant needs a name.");
  });

  it("has nothing to save once a tag is ticked back", async () => {
    open("settings");
    const vegan = await field("Vegan");
    fireEvent.click(vegan);
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    fireEvent.click(vegan);
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it("asks before leaving unsaved changes", async () => {
    const { router } = open("settings");
    type(await field("City"), "Basel");

    await act(() => router.navigate(`/manage/${SLUG}/rules`));
    expect(screen.getByRole("dialog", { name: "Leave without saving?" })).toBeTruthy();
    click("Keep editing");
    expect(router.state.location.pathname).toBe(`/manage/${SLUG}/settings`);
    expect((await field("City")).value).toBe("Basel");

    await act(() => router.navigate(`/manage/${SLUG}/rules`));
    click("Leave");
    expect(await field("Largest group")).toBeTruthy();
  });
});

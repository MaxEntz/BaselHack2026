// What Studio shows for a chat state (#141): pure functions, no React. The fixed sentences come
// from here and from feed.tsx, because the model's text can vary (`llm` tests assert facts).
import type { CardEvent, ChatItem, ChatState, StepGroup, StepName, StepState } from "../chat/state.ts";
import type { BuildAgentOutput, LiveEvent, Service } from "../types.ts";
import { plural } from "../cards/format.ts";
import { lastSeating as lastSeatingOf, toMinutes } from "../management/services.ts";
import { momentSentence } from "../moments.ts";
import { PUBLISH } from "./publish.ts";

export type Phase = "address" | "describe" | "building" | "review" | "live";

export function phaseOf(state: ChatState): Phase {
  if (state.status === "live" || state.status === "down") return "live";
  if (state.status === "building") {
    const group = buildGroup(state.items);
    return state.busy && group?.turn === state.turn ? "building" : "review";
  }
  return state.address ? "describe" : "address";
}

// A website's address in a message, and how many other words came with it.
export type FoundAddress = { address: string; host: string; words: number };

// Not an e-mail's domain, nor a file's name ("our menu.pdf").
const ADDRESS = /(?<![@\w.-])(?:https?:\/\/)?((?:[a-z0-9-]+\.)+[a-z]{2,}|localhost|\d{1,3}(?:\.\d{1,3}){3})(:\d+)?(\/\S*)?/gi;
const FILE = /\.(pdf|jpe?g|png|gif|webp|svg|docx?|xlsx?|csv|txt|zip)$/i;

export function parseAddress(text: string): FoundAddress | null {
  const match = [...text.trim().matchAll(ADDRESS)].find((found) => !FILE.test(found[1]));
  if (!match) return null;
  const rest = text.replace(match[0], "").replace(/^[\s,.:;-]+/, "").trim();
  const host = `${match[1].toLowerCase().replace(/^www\./, "")}${match[2] ?? ""}`;
  return { address: match[0].replace(/[.,;:]+$/, ""), host, words: rest.split(/\s+/).filter(Boolean).length };
}

// Three other words are enough to build straight away.
export const describesItself = (found: FoundAddress) => found.words >= 3;

export const askForDetails = (host: string) =>
  `I can read **${host}**. Websites rarely say how the room works, so tell me first: how many seats and tables, and what's the largest group you take?`;

export const SKIP = "Skip this, use the defaults";

// What the assistant receives once the merchant answered: it needs the address and the
// description in one message to call build_agent.
export function buildMessage(address: string, answer: string): string {
  return /^skip\b/i.test(answer.trim())
    ? `${address}. No description: use the defaults.`
    : `${address}. ${answer.trim()}`;
}

export const PLACEHOLDERS: Record<Phase, string> = {
  address: "Paste your website address",
  describe: "Seats, tables, the largest group you take",
  building: "Ask for a change",
  review: "Ask for a change",
  live: "Ask for a change",
};
export const FIRST_PLACEHOLDER = "Paste your website address and tell me how you work. 40 seats, groups up to 8…";

// The empty screen: nothing was said yet, and no merchant came back.
export const isEmpty = (state: ChatState) => state.turn === 0 && !state.merchant;

function isDeployOnly(group: StepGroup): boolean {
  return Object.keys(group.steps).length === 1 && group.steps.deploy !== undefined;
}

function buildGroup(items: readonly ChatItem[]): StepGroup | undefined {
  return items.findLast((item): item is StepGroup => item.type === "steps" && !isDeployOnly(item));
}

export function profileOf(items: readonly ChatItem[]): BuildAgentOutput | null {
  const card = items.findLast(
    (item) => item.type === "card" && item.card.kind === "profile",
  );
  return card?.type === "card" && card.card.kind === "profile" ? card.card.data : null;
}

// The site's name once it is known, its address until then.
export const siteName = (state: ChatState) => state.merchant?.name ?? state.site;

export function isBuilt(state: ChatState): boolean {
  if (state.merchant) return true;
  return buildGroup(state.items)?.steps.provision?.status === "done";
}

export type Pill = { label: string; live: boolean };

export function pillOf(state: ChatState): Pill | null {
  if (!siteName(state)) return null;
  if (state.status === "live") return { label: "Live", live: true };
  if (state.status === "down") return { label: "Paused", live: false };
  return { label: isBuilt(state) ? "Hidden from guests" : "Not built yet", live: false };
}

export type FeedEntry =
  | { key: string; kind: "user"; text: string; first: boolean }
  | { key: string; kind: "bot"; text: string }
  | { key: string; kind: "steps"; title: string; steps: StepGroup["steps"]; found: number | null }
  | { key: string; kind: "review"; data: BuildAgentOutput; loginUrl: string | null; pages: number | null }
  | { key: string; kind: "live" }
  | { key: string; kind: "moment"; text: string }
  | { key: string; kind: "card"; card: Exclude<CardEvent, { kind: "profile" }> }
  | { key: string; kind: "ping"; event: LiveEvent }
  | { key: string; kind: "error"; message: string };

// What the feed shows. The model's text after a build or a publish gives way to the fixed
// sentences, and publishing updates the build's card instead of adding one, as in the mockup.
export function feedOf(state: ChatState): FeedEntry[] {
  const { items } = state;
  const build = buildGroup(items);
  const data = profileOf(items);
  const firstUser = items.find((item) => item.type === "user")?.id;
  const deploy = items.reduce<StepState | undefined>(
    (latest, item) => (item.type === "steps" && item.steps.deploy) || latest,
    undefined,
  );
  const entries: FeedEntry[] = [];
  for (const item of items) {
    const key = item.id;
    switch (item.type) {
      case "user":
        entries.push({ key, kind: "user", text: item.text, first: key === firstUser });
        break;
      case "assistant":
        // Studio greets with its own screen: the onboarding welcome is empty.
        if (item.text && !followsSteps(items, item) && !followsMoment(items, item)) {
          entries.push({ key, kind: "bot", text: item.text });
        }
        break;
      case "steps":
        if (isDeployOnly(item)) {
          if (item.steps.deploy?.status === "done") entries.push({ key, kind: "live" });
          if (item.steps.deploy?.status === "error") {
            entries.push({ key, kind: "error", message: item.steps.deploy.detail ?? "Publishing stopped." });
          }
        } else {
          entries.push({
            key,
            kind: "steps",
            title: `Building ${siteName(state) ?? "your site"}`,
            steps: item === build && deploy ? { ...item.steps, deploy } : item.steps,
            found: item === build && data ? data.facts.length : null,
          });
        }
        break;
      case "card":
        if (item.card.kind === "profile") {
          entries.push({
            key,
            kind: "review",
            data: item.card.data,
            loginUrl: item.card.login_url ?? null,
            pages: pagesRead(build),
          });
        } else if (item.card.kind === "view") {
          // Not a message: Studio switches its canvas (#152). A moment says what it shows.
          if (item.card.data.moment) {
            const text = momentSentence(item.card.data.moment, data?.profile.services ?? []);
            entries.push({ key, kind: "moment", text });
          }
        } else if (!(item.card.kind === "confirmation" && followsSteps(items, item))) {
          entries.push({ key, kind: "card", card: item.card });
        }
        break;
      case "notification":
        entries.push({ key, kind: "ping", event: item.event });
        break;
      case "error":
        entries.push({ key, kind: "error", message: item.message });
        break;
    }
  }
  return entries;
}

// After a preview of a moment of the same turn: the fixed sentence has said it.
function followsMoment(items: readonly ChatItem[], item: ChatItem): boolean {
  return items
    .slice(0, items.indexOf(item))
    .some(
      (other) =>
        other.turn === item.turn &&
        other.type === "card" &&
        other.card.kind === "view" &&
        Boolean(other.card.data.moment),
    );
}

// After a steps card of the same turn, unless a step failed: then the model explains it.
function followsSteps(items: readonly ChatItem[], item: ChatItem): boolean {
  const index = items.indexOf(item);
  const group = items
    .slice(0, index)
    .findLast((other): other is StepGroup => other.type === "steps" && other.turn === item.turn);
  if (!group) return false;
  const steps = Object.values(group.steps) as StepState[];
  return !steps.some((step) => step.status === "error");
}

function pagesRead(group: StepGroup | undefined): number | null {
  const count = group?.steps.crawl?.detail?.match(/^Read (\d+) page/)?.[1];
  return count ? Number(count) : null;
}

const STEP_NOTES: Partial<Record<StepName, string>> = {
  deploy: "Agents get real answers now.",
};

// A step's note. The server words them, except what only the review card knows or Studio's own.
export function stepNote(name: StepName, step: StepState, found: number | null): string | null {
  if (name === "extract" && step.status === "done") {
    return found === null ? null : `Found ${plural(found, "detail")}.`;
  }
  if (step.status === "done" && STEP_NOTES[name]) return STEP_NOTES[name];
  return step.detail ?? null;
}

// The last start of the evening, by the management site's rule (§6).
export function lastSeating(services: readonly Service[]): string | null {
  const last = services.toSorted((a, b) => toMinutes(a.start) - toMinutes(b.start)).at(-1);
  return last ? lastSeatingOf(last) : null;
}

// The mockup's other chips need what isn't there yet, and come with it: "A table for {n}?", the
// cancellation policy, the dark site and "Say covers" with #152, "Send a test booking" with
// #151 and #152, "Show during service" with #154.
export function chipsOf(state: ChatState, phase: Phase, examples: readonly string[]): string[] {
  const asked = new Set(
    state.items.flatMap((item) => (item.type === "user" ? [item.text] : [])),
  );
  const fresh = (chips: string[]) => chips.filter((chip) => !asked.has(chip));
  switch (phase) {
    case "address":
      return state.busy ? [] : [...examples];
    case "describe":
      return [SKIP];
    case "building":
      return [];
    case "review": {
      const profile = profileOf(state.items)?.profile;
      const last = profile ? lastSeating(profile.services) : null;
      return fresh([
        ...(last ? [`Block ${last} tonight`] : []),
        ...(profile ? [`Set max party size to ${profile.max_party_size + 2}`] : []),
        "Show during service",
        "What bookings do I have tonight?",
        PUBLISH,
      ]);
    }
    case "live":
      return fresh(["Show during service", "What bookings do I have tonight?", "How busy are we tonight?"]);
  }
}

// Two letters per site: the first letters of its words, without the articles.
export function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter((word) => !/^(le|la|les|chez|the)$/i.test(word))
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
  return (letters || name.slice(0, 2)).padEnd(2, name[1] ?? "");
}

export type CanvasView = "agent" | "manage" | "both" | "public";

// How the canvas draws its frames (#151): at their real width, scaled to fit.
const FRAME_WIDTH = 1000;
const FRAME_WIDTH_ACROSS = 760;
// "Both" stands side by side from this much room.
const ACROSS_FROM = 1240;
const GAP = 24;

export type Space = { width: number; height: number };

export type Fit = { width: number; scale: number; across: boolean; stacked: boolean };

export function fitOf(view: CanvasView, space: Space, narrow: boolean): Fit {
  const both = view === "both" && !narrow;
  const across = both && space.width >= ACROSS_FROM;
  const width = narrow && space.width > 0 ? space.width : across ? FRAME_WIDTH_ACROSS : FRAME_WIDTH;
  const room = across ? (space.width - GAP) / 2 : space.width;
  return {
    width,
    scale: room > 0 ? Math.min(1, room / width) : 1,
    across,
    stacked: both && !across,
  };
}

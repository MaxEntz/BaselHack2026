import type { ChatEvent, ConversationItem, LiveEvent, SitePage } from "../types.ts";

export type CardEvent = Extract<ChatEvent, { type: "card" }>;
type StepEvent = Extract<ChatEvent, { type: "step" }>;
export type StepName = StepEvent["step"];
export type StepState = Omit<StepEvent, "type" | "step">;
export type AgentStatus = "building" | "live" | "down";

type Base = { id: string; turn: number };
type UserText = Base & { type: "user"; text: string };
type AssistantText = Base & { type: "assistant"; text: string };
type Card = Base & { type: "card"; card: CardEvent };
export type StepGroup = Base & {
  type: "steps";
  steps: Partial<Record<StepName, StepState>>;
};
type Notification = Base & { type: "notification"; event: LiveEvent };
type ErrorItem = Base & { type: "error"; message: string };

export type ChatItem =
  | UserText
  | AssistantText
  | Card
  | StepGroup
  | Notification
  | ErrorItem;

export type Merchant = { slug: string; name: string };

export type ChatState = {
  items: readonly ChatItem[];
  turn: number;
  busy: boolean;
  // Label of the tool currently running ("Checking tonight's bookings…").
  activity: string | null;
  merchant: Merchant | null;
  status: AgentStatus | null;
  // The site being onboarded, until the agent has a name.
  site: string | null;
  // The address given, while Studio asks how the merchant works.
  address: string | null;
  // The public site's pages, as the crawl of this conversation read them.
  sitePages: readonly SitePage[];
  nextId: number;
};

export type ChatAction =
  | { type: "send"; text: string; site?: string }
  // An address with nothing else: Studio asks for the details itself, with no model call.
  | { type: "details"; text: string; address: string; site: string; reply: string }
  | { type: "status"; status: AgentStatus }
  | { type: "reset"; welcome: string }
  | { type: "chat"; event: ChatEvent }
  | { type: "live"; event: LiveEvent }
  | { type: "failed"; message: string }
  // The platform says which merchant the session works on (GET /session, #88).
  | { type: "attached"; merchant: Merchant; welcome: string }
  // The conversation the platform kept (#140): it replaces what is shown.
  | { type: "restore"; items: readonly ConversationItem[] };

export function initialChatState(
  welcome: string,
  merchant: Merchant | null,
): ChatState {
  return {
    items: [{ id: "welcome", turn: 0, type: "assistant", text: welcome }],
    turn: 0,
    busy: false,
    activity: null,
    merchant,
    status: merchant ? "live" : null,
    site: null,
    address: null,
    sitePages: [],
    nextId: 1,
  };
}

// Pure reducer: every action returns new arrays and objects, never mutates.
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "send": {
      const turn = state.turn + 1;
      return append(
        { ...state, turn, busy: true, activity: null, address: null, site: action.site ?? state.site },
        { type: "user", turn, text: action.text },
      );
    }
    case "details": {
      const turn = state.turn + 1;
      const asked = { ...state, turn, address: action.address, site: action.site };
      return append(append(asked, { type: "user", turn, text: action.text }), {
        type: "assistant",
        turn,
        text: action.reply,
      });
    }
    case "status":
      return { ...state, status: action.status };
    case "reset":
      return initialChatState(action.welcome, null);
    case "live":
      return append(state, {
        type: "notification",
        turn: state.turn,
        event: action.event,
      });
    case "failed":
      return append(
        { ...interruptBuild(state), busy: false, activity: null },
        { type: "error", turn: state.turn, message: action.message },
      );
    case "chat":
      return applyChatEvent(state, action.event);
    case "attached":
      return attach(state, action.merchant, action.welcome);
    case "restore":
      return action.items.reduce<ChatState>(
        (next, item) => append(next, { ...item, turn: 0 } as NewItem),
        { ...state, items: [], turn: 0 },
      );
  }
}

// A reload keeps the server session but not the chat: before the first message,
// the onboarding welcome gives way to the operations one.
function attach(state: ChatState, merchant: Merchant, welcome: string): ChatState {
  if (state.merchant?.slug === merchant.slug) return state;
  const next: ChatState = { ...state, merchant, status: "live" };
  if (state.turn > 0 || state.items[0]?.id !== "welcome") return next;
  return replaceAt(next, 0, { id: "welcome", turn: 0, type: "assistant", text: welcome });
}

function applyChatEvent(state: ChatState, event: ChatEvent): ChatState {
  switch (event.type) {
    case "text":
      return appendText({ ...state, activity: null }, event.delta);
    case "tool_start":
      return { ...state, activity: event.label };
    case "site_page":
      return {
        ...state,
        sitePages: [
          ...state.sitePages.filter((page) => page.path !== event.page.path),
          event.page,
        ],
      };
    case "step":
      return applyStep(state, event);
    case "card": {
      const next = { ...state, activity: null };
      if (event.kind === "profile") {
        const { slug, name } = event.data.profile;
        next.merchant = { slug, name };
        // Hidden from guests until the owner publishes (#136).
        next.status = event.data.status;
      }
      return append(next, { type: "card", turn: state.turn, card: event });
    }
    case "error":
      return append(
        { ...state, activity: null },
        { type: "error", turn: state.turn, message: event.message },
      );
    case "done":
      return { ...interruptBuild(state), busy: false, activity: null };
  }
}

// A turn that ends with a step still running (stream cut, or `done` before the last step)
// stopped the build: the server undid it (#86). No spinner or "Building" pill may outlive it.
function interruptBuild(state: ChatState): ChatState {
  const index = lastTurnItemIndex(state, (item) => item.type === "steps");
  const group = index === -1 ? null : state.items[index];
  let next = state;
  if (group?.type === "steps") {
    const entries = Object.entries(group.steps) as [StepName, StepState][];
    if (entries.some(([, step]) => step.status === "running")) {
      const steps = Object.fromEntries(
        entries.map(([name, step]) => [
          name,
          step.status === "running" ? { ...step, status: "error", detail: "Interrupted" } : step,
        ]),
      );
      next = replaceAt(state, index, { ...group, steps });
    }
  }
  // A build that reached `deploy` waits for its owner, hidden from guests (#136): its pill
  // stays. One stopped earlier in this turn left nothing, as the server undid it.
  const deploy = group?.type === "steps" ? group.steps.deploy?.status : undefined;
  if (next.status !== "building" || group?.type !== "steps" || deploy === "waiting") return next;
  if (isPublishing(state, "deploy")) return next;
  return { ...next, status: next.merchant ? "live" : null };
}

// `deploy` alone in this turn: publishing an agent the build left hidden (#136).
function isPublishing(state: ChatState, step: StepName): boolean {
  if (step !== "deploy") return false;
  const index = lastTurnItemIndex(state, (item) => item.type === "steps");
  const group = index === -1 ? null : state.items[index];
  return group?.type === "steps" && Object.keys(group.steps).every((name) => name === "deploy");
}

// Deltas extend the turn's last assistant message, unless something else
// (a card, steps) was shown after it. Live notifications don't split a reply.
function appendText(state: ChatState, delta: string): ChatState {
  const index = lastTurnItemIndex(state, (item) => item.type !== "notification");
  const last = index === -1 ? null : state.items[index];
  if (last?.type === "assistant") {
    return replaceAt(state, index, { ...last, text: last.text + delta });
  }
  return append(state, { type: "assistant", turn: state.turn, text: delta });
}

// All steps of one build are grouped into a single StepGroup per turn.
function applyStep(state: ChatState, event: StepEvent): ChatState {
  const { type: _type, step, ...stepState } = event;
  let status: AgentStatus | null = "building";
  // A failed rebuild leaves an onboarded merchant's agent live; a failed first build leaves no
  // agent at all (the server undoes it), as when the stream is cut (#86). A failed publish
  // leaves it hidden.
  if (event.status === "error") status = isPublishing(state, step) ? "building" : state.merchant ? "live" : null;
  else if (step === "deploy" && event.status === "done") status = "live";
  const next: ChatState = { ...state, status };
  const index = lastTurnItemIndex(state, (item) => item.type === "steps");
  const group = index === -1 ? null : state.items[index];
  if (group?.type === "steps") {
    return replaceAt(next, index, {
      ...group,
      steps: { ...group.steps, [step]: stepState },
    });
  }
  return append(next, {
    type: "steps",
    turn: state.turn,
    steps: { [step]: stepState },
  });
}

// Distributes over the union so each item type keeps its own fields.
type WithoutId<T> = T extends ChatItem ? Omit<T, "id"> : never;
type NewItem = WithoutId<ChatItem>;

function append(state: ChatState, item: NewItem): ChatState {
  return {
    ...state,
    items: [...state.items, { ...item, id: String(state.nextId) } as ChatItem],
    nextId: state.nextId + 1,
  };
}

function replaceAt(state: ChatState, index: number, item: ChatItem): ChatState {
  return { ...state, items: state.items.with(index, item) };
}

function lastTurnItemIndex(
  state: ChatState,
  match: (item: ChatItem) => boolean,
): number {
  for (let i = state.items.length - 1; i >= 0; i--) {
    const item = state.items[i];
    if (item.turn !== state.turn) return -1;
    if (match(item)) return i;
  }
  return -1;
}

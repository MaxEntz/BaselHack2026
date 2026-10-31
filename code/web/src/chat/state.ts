import type { ChatEvent, LiveEvent } from "../types.ts";

export type CardEvent = Extract<ChatEvent, { type: "card" }>;
export type StepEvent = Extract<ChatEvent, { type: "step" }>;
export type StepName = StepEvent["step"];
export type StepState = Omit<StepEvent, "type" | "step">;
export type AgentStatus = "building" | "live" | "down";

type Base = { id: string; turn: number };
export type UserText = Base & { type: "user"; text: string };
export type AssistantText = Base & { type: "assistant"; text: string };
export type Card = Base & { type: "card"; card: CardEvent };
export type StepGroup = Base & {
  type: "steps";
  steps: Partial<Record<StepName, StepState>>;
};
export type Notification = Base & { type: "notification"; event: LiveEvent };
export type ErrorItem = Base & { type: "error"; message: string };

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
  nextId: number;
};

export type ChatAction =
  | { type: "send"; text: string }
  | { type: "chat"; event: ChatEvent }
  | { type: "live"; event: LiveEvent }
  | { type: "failed"; message: string };

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
    nextId: 1,
  };
}

// Pure reducer: every action returns new arrays and objects, never mutates.
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "send": {
      const turn = state.turn + 1;
      return append(
        { ...state, turn, busy: true, activity: null },
        { type: "user", turn, text: action.text },
      );
    }
    case "live":
      return append(state, {
        type: "notification",
        turn: state.turn,
        event: action.event,
      });
    case "failed":
      return append(
        { ...state, busy: false, activity: null },
        { type: "error", turn: state.turn, message: action.message },
      );
    case "chat":
      return applyChatEvent(state, action.event);
  }
}

function applyChatEvent(state: ChatState, event: ChatEvent): ChatState {
  switch (event.type) {
    case "text":
      return appendText({ ...state, activity: null }, event.delta);
    case "tool_start":
      return { ...state, activity: event.label };
    case "step":
      return applyStep(state, event);
    case "card": {
      const next = { ...state, activity: null };
      if (event.kind === "profile") {
        const { slug, name } = event.data.profile;
        next.merchant = { slug, name };
        next.status = "live";
      }
      return append(next, { type: "card", turn: state.turn, card: event });
    }
    case "error":
      return append(
        { ...state, activity: null },
        { type: "error", turn: state.turn, message: event.message },
      );
    case "done":
      return { ...state, busy: false, activity: null };
  }
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
  let status: AgentStatus = "building";
  // A failed rebuild leaves an onboarded merchant's agent live.
  if (event.status === "error") status = state.merchant ? "live" : "down";
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

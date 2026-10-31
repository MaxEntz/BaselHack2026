import { httpClient, type LiveHandlers } from "../chat/client.ts";
import type {
  AgentIntent,
  AgentTryOutput,
  PublicSite,
  AgentRequests,
  AgentStatusOutput,
  AssistantConversation,
  BlockedTimes,
  Dashboard,
  DaySlots,
  FloorPlanInput,
  FloorPlanOutput,
  GuestsOutput,
  Hours,
  HoursInput,
  ManagedReservation,
  ManagementError,
  MerchantBookingInput,
  MerchantCancelOutput,
  MerchantOverview,
  MerchantSettings,
  MoveBookingInput,
  ReservationDay,
  Rules,
  RulesInput,
  SearchOutput,
  SettingsInput,
} from "../types.ts";

export type SlotsQuery = { date: string; partySize: number; ignore?: string };
export type RequestsQuery = { date: string; refused: boolean };

// The management site's calls, one per /merchants/<slug> route (#129). Each
// throws `ManagementRequestError` when the platform refuses or can't answer.
// Bodies must hold only their input's fields: the platform refuses any other
// (send a `RulesInput`, not the `Rules` it answered).
export type ManagementClient = {
  merchant: (slug: string, signal?: AbortSignal) => Promise<MerchantOverview>;
  // Today by the platform's clock, which `merchant().now` also gives.
  dashboard: (slug: string, signal?: AbortSignal) => Promise<Dashboard>;
  reservations: (slug: string, date: string, signal?: AbortSignal) => Promise<ReservationDay>;
  addBooking: (slug: string, body: MerchantBookingInput) => Promise<ManagedReservation>;
  moveBooking: (slug: string, id: string, body: MoveBookingInput) => Promise<ManagedReservation>;
  cancelBooking: (slug: string, id: string) => Promise<MerchantCancelOutput>;
  slots: (slug: string, query: SlotsQuery, signal?: AbortSignal) => Promise<DaySlots>;
  guests: (slug: string, q: string, signal?: AbortSignal) => Promise<GuestsOutput>;
  search: (slug: string, q: string, signal?: AbortSignal) => Promise<SearchOutput>;
  hours: (slug: string, signal?: AbortSignal) => Promise<Hours>;
  saveHours: (slug: string, body: HoursInput) => Promise<Hours>;
  tables: (slug: string, signal?: AbortSignal) => Promise<FloorPlanOutput>;
  saveTables: (slug: string, body: FloorPlanInput) => Promise<FloorPlanOutput>;
  rules: (slug: string, signal?: AbortSignal) => Promise<Rules>;
  saveRules: (slug: string, body: RulesInput) => Promise<Rules>;
  settings: (slug: string, signal?: AbortSignal) => Promise<MerchantSettings>;
  saveSettings: (slug: string, body: SettingsInput) => Promise<MerchantSettings>;
  blocked: (slug: string, signal?: AbortSignal) => Promise<BlockedTimes>;
  // Sets the day's whole set of blocked times (HH:MM).
  setBlocked: (slug: string, date: string, times: string[]) => Promise<BlockedTimes>;
  // The public site as read during the build; no pages for a merchant built before #151.
  site: (slug: string, signal?: AbortSignal) => Promise<PublicSite>;
  // One real frame through the BATP node, as a test agent (#151).
  tryAgent: (slug: string, intent: AgentIntent) => Promise<AgentTryOutput>;
  // Goes live, from what a build left hidden (#136).
  publish: (slug: string) => Promise<MerchantOverview>;
  pauseAgent: (slug: string) => Promise<AgentStatusOutput>;
  resumeAgent: (slug: string) => Promise<AgentStatusOutput>;
  agentRequests: (slug: string, query: RequestsQuery, signal?: AbortSignal) => Promise<AgentRequests>;
  // The owner's conversation with the assistant, and its session (#140).
  assistant: (slug: string, signal?: AbortSignal) => Promise<AssistantConversation>;
  // Guests' changes, live (GET /events); returns the unsubscribe function.
  subscribe: (slug: string, handlers: LiveHandlers) => () => void;
};

export class ManagementRequestError extends Error {
  // 0 when the platform couldn't be reached.
  readonly status: number;
  readonly body: ManagementError;

  constructor(status: number, body: ManagementError) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
}

const UNREACHABLE = "I can't reach the platform right now.";
const NO_ANSWER = "The platform couldn't answer.";

async function call<T>(
  method: "GET" | "POST" | "PUT" | "PATCH",
  slug: string,
  path: string,
  { body, signal }: { body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/merchants/${encodeURIComponent(slug)}${path}`, {
      method,
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      // The owner's session cookie (#132).
      credentials: "same-origin",
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ManagementRequestError(0, { error: "agent_unavailable", message: UNREACHABLE });
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ManagementRequestError(response.status, errorBody(payload));
  }
  return payload as T;
}

function errorBody(payload: unknown): ManagementError {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    "message" in payload &&
    typeof payload.message === "string"
  ) {
    return payload as ManagementError;
  }
  return { error: "agent_unavailable", message: NO_ANSWER };
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  return `?${search}`;
}

const id = (value: string) => encodeURIComponent(value);

export const httpManagementClient: ManagementClient = {
  merchant: (slug, signal) => call("GET", slug, "", { signal }),
  assistant: (slug, signal) => call("GET", slug, "/assistant", { signal }),
  dashboard: (slug, signal) => call("GET", slug, "/dashboard", { signal }),
  reservations: (slug, date, signal) =>
    call("GET", slug, `/reservations${query({ date })}`, { signal }),
  addBooking: (slug, body) => call("POST", slug, "/reservations", { body }),
  moveBooking: (slug, reservation, body) =>
    call("PATCH", slug, `/reservations/${id(reservation)}`, { body }),
  cancelBooking: (slug, reservation) =>
    call("POST", slug, `/reservations/${id(reservation)}/cancel`),
  slots: (slug, { date, partySize, ignore }, signal) =>
    call("GET", slug, `/slots${query({ date, party_size: partySize, ignore })}`, { signal }),
  guests: (slug, q, signal) => call("GET", slug, `/guests${query({ q })}`, { signal }),
  search: (slug, q, signal) => call("GET", slug, `/search${query({ q })}`, { signal }),
  hours: (slug, signal) => call("GET", slug, "/hours", { signal }),
  saveHours: (slug, body) => call("PUT", slug, "/hours", { body }),
  tables: (slug, signal) => call("GET", slug, "/tables", { signal }),
  saveTables: (slug, body) => call("PUT", slug, "/tables", { body }),
  rules: (slug, signal) => call("GET", slug, "/rules", { signal }),
  saveRules: (slug, body) => call("PUT", slug, "/rules", { body }),
  settings: (slug, signal) => call("GET", slug, "/settings", { signal }),
  saveSettings: (slug, body) => call("PUT", slug, "/settings", { body }),
  blocked: (slug, signal) => call("GET", slug, "/blocked", { signal }),
  setBlocked: (slug, date, times) =>
    call("PUT", slug, `/blocked${query({ date })}`, { body: { times } }),
  site: (slug, signal) => call("GET", slug, "/site", { signal }),
  tryAgent: (slug, intent) => call("POST", slug, "/agent/try", { body: { intent } }),
  publish: (slug) => call("POST", slug, "/publish"),
  pauseAgent: (slug) => call("POST", slug, "/agent/pause"),
  resumeAgent: (slug) => call("POST", slug, "/agent/resume"),
  agentRequests: (slug, { date, refused }, signal) =>
    call("GET", slug, `/agent/requests${query({ date, refused })}`, { signal }),
  subscribe: (slug, handlers) => httpClient.subscribe(slug, handlers),
};

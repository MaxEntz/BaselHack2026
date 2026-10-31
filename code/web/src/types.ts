export type Tag =
  | "gluten-free"
  | "vegetarian"
  | "vegan"
  | "halal"
  | "kosher"
  | "terrace"
  | "kid-friendly";

export type PriceLevel = "€" | "€€" | "€€€" | "€€€€";
export type Via = "claude" | "merchant" | "website";

export type Service = { start: string; end: string };
export type TableSpec = { label: string; seats: number };
export type Location = {
  city: string | null;
  lat: number | null;
  lng: number | null;
};

export type MerchantProfile = {
  slug: string;
  name: string;
  website: string;
  vertical: "restaurant";
  location: Location | null;
  seats: number;
  services: Service[];
  max_party_size: number;
  tables: TableSpec[];
  tags: Tag[];
  price_level: PriceLevel;
  cancellation_policy: string | null;
  fallback_url: string;
};

export type GeneratedTool = { name: string; description: string };

export type BuildAgentOutput = {
  profile: MerchantProfile;
  status: "live";
  assumptions: string[];
  tools: GeneratedTool[];
  mcp_url: string;
};

export type AdminReservationRow = {
  id: string;
  time: string;
  guest_name: string;
  party_size: number;
  table_label: string;
  via: Via;
  status: "confirmed" | "cancelled";
};

export type Stats = {
  bookings: number;
  guests: number;
  seats: number;
  blocked: string[];
};

export type Policy = {
  max_party_size: number;
  slot_length_min: number;
  services: Service[];
};

// GET /session?session_id=: the merchant the chat session works on (#88).
export type SessionMerchant = { slug: string; name: string };
export type SessionOutput = { merchant: SessionMerchant | null };

// POST /chat: event: <type> + data: <JSON>, separated by a blank line.
export type ChatEvent =
  | { type: "text"; delta: string }
  | { type: "tool_start"; tool: string; label: string }
  | {
      type: "step";
      step: "crawl" | "extract" | "validate" | "provision" | "deploy";
      status: "running" | "done" | "error";
      detail?: string | null;
      duration_ms?: number | null;
    }
  | { type: "card"; kind: "profile"; data: BuildAgentOutput }
  | {
      type: "card";
      kind: "reservations";
      data: { date: string; reservations: AdminReservationRow[] };
    }
  | { type: "card"; kind: "stats"; data: Stats & { date: string } }
  | { type: "card"; kind: "policy"; data: Policy }
  | { type: "card"; kind: "confirmation"; data: { text: string } }
  | { type: "error"; message: string }
  | { type: "done" };

// GET /events?merchant=<slug>: EventSource notifications. Only guests' changes:
// the merchant's own are not sent. `via` is the channel the booking was made
// through; `date` (YYYY-MM-DD) is its day in the merchant's timezone.
export type LiveEvent = {
  type: "notification";
  kind: "new_booking" | "updated" | "cancelled";
  via: Via;
  date: string;
  reservation: AdminReservationRow;
};

// Management site (#129): the platform's /merchants/<slug> routes, mirrored from
// core/models.py. Days are YYYY-MM-DD and times HH:MM, local to the merchant
// (`MerchantOverview.timezone`); instants are ISO 8601 with an offset.
export type MerchantStatus = "building" | "live" | "down";
export type CancelledBy = "guest" | "merchant";
export type MerchantCancelOutcome =
  | "notified"
  | "ended"
  | "own_booking"
  | "guest_cancelled"
  | "already_cancelled";
export type ActivityKind =
  | "new_booking"
  | "moved"
  | "cancelled"
  | "blocked"
  | "reopened"
  | "saved"
  | "paused"
  | "resumed";
export type ProblemCode =
  | "no_services"
  | "service_incomplete"
  | "service_too_short"
  | "services_overlap"
  | "no_tables"
  | "duplicate_table"
  | "table_without_seats"
  | "too_many_seats"
  | "table_in_use"
  | "table_too_small"
  | "group_too_small"
  | "name_required"
  | "booking_page_off_site";

// GET /merchants/<slug>
export type MerchantOverview = {
  slug: string;
  name: string;
  city: string | null;
  price_level: PriceLevel;
  tags: Tag[];
  website: string;
  status: MerchantStatus;
  timezone: string;
  agent_address: string;
  mcp_url: string;
  // Absolute: https://<web host>/manage/<slug>.
  management_url: string;
  // What the owner asked to be called; null until known.
  owner_name: string | null;
  // Built from one of the demo sites (code/sites/).
  demo: boolean;
};

export type ManagedReservation = {
  id: string;
  date: string;
  time: string;
  guest_name: string;
  party_size: number;
  table_label: string;
  via: Via;
  status: "confirmed" | "cancelled";
  cancelled_by: CancelledBy | null;
  created_at: string;
};

export type DayHistory = {
  date: string;
  bookings: number;
  guests: number;
  agent_guests: number;
  cancelled: number;
};
export type DayGuests = { date: string; guests: number };
export type ChannelCount = { via: Via; bookings: number };
export type RoomTable = { label: string; seats: number; booked: boolean };
export type Room = {
  service: Service;
  seats: number;
  guests: number;
  tables: RoomTable[];
  party_size: number;
  // Never a blocked start.
  next_free: string | null;
  largest_group: number;
};
export type ActivityItem = {
  id: number;
  at: string;
  kind: ActivityKind;
  // Worded by the server when read, relative to the dashboard's date.
  text: string;
  source: Via;
  reservation_id: string | null;
};

// GET /merchants/<slug>/dashboard?date=, `date` being the merchant's today.
export type Dashboard = {
  date: string;
  history: DayHistory[];
  next_days: DayGuests[];
  channels: ChannelCount[];
  room: Room;
  activity: ActivityItem[];
};

// GET /merchants/<slug>/reservations?date=
export type ReservationDay = {
  date: string;
  services: Service[];
  // Every start of the day on the grid, past ones included.
  starts: string[];
  slot_length_min: number;
  largest_group: number;
  blocked: string[];
  reservations: ManagedReservation[];
};

// POST /merchants/<slug>/reservations, PATCH …/reservations/<id>
export type MerchantBookingInput = {
  starts_at: string;
  party_size: number;
  guest_name: string;
  idempotency_key: string;
};
export type MoveBookingInput = { starts_at: string; idempotency_key: string };
// POST …/reservations/<id>/cancel
export type MerchantCancelOutput = {
  reservation: ManagedReservation;
  outcome: MerchantCancelOutcome;
};

// GET /merchants/<slug>/slots?date=&party_size=&ignore=
export type DaySlot = {
  starts_at: string;
  time: string;
  until: string;
  table: TableSpec | null;
  blocked: boolean;
};
export type DaySlots = { date: string; party_size: number; slots: DaySlot[] };

// GET …/guests?q=, GET …/search?q=
export type GuestRow = {
  guest_name: string;
  reservation_id: string;
  date: string;
  time: string;
  party_size: number;
  bookings: number;
  via: Via;
};
export type GuestsOutput = { guests: GuestRow[] };
export type SearchOutput = { reservations: ManagedReservation[] };

// GET/PUT …/hours, …/tables, …/rules, …/settings. The form bodies are
// structural: the server checks their rules and answers the `Problem`.
export type Hours = { services: Service[] };
export type ServiceDraft = { start?: string | null; end?: string | null };
export type HoursInput = { services: ServiceDraft[] };
export type FloorPlanOutput = { tables: TableSpec[] };
export type TableDraft = { label: string; seats: number };
export type FloorPlanInput = { tables: TableDraft[] };
export type Rules = {
  max_party_size: number;
  slot_length_min: number;
  fallback_url: string;
  cancellation_policy: string | null;
  tools: GeneratedTool[];
};
export type RulesInput = {
  max_party_size: number;
  fallback_url: string;
  cancellation_policy?: string | null;
};
export type MerchantSettings = {
  name: string;
  city: string | null;
  website: string;
  price_level: PriceLevel;
  tags: Tag[];
};
export type SettingsInput = {
  name: string;
  city?: string | null;
  price_level: PriceLevel;
  tags: Tag[];
};

// GET …/blocked, PUT …/blocked?date= with the day's whole set
export type BlockedTime = { starts_at: string; date: string; time: string };
export type BlockedTimes = { blocked: BlockedTime[] };

// POST …/agent/pause and …/agent/resume, GET …/agent/requests?date=&refused=
export type AgentStatusOutput = { status: MerchantStatus };
export type AgentRequest = {
  id: number;
  at: string;
  agent_name: string;
  intent: string;
  status: number;
  refused: boolean;
  title: string;
  text: string;
  // The frames as sent, JSON.
  request: string;
  response: string;
};
export type AgentRequests = { requests: AgentRequest[] };

// The error body of every /merchants/<slug> route: the tools' codes (§8.4)
// and the management site's own.
export type ErrorCode =
  | "agent_unavailable"
  | "party_too_large"
  | "slot_unavailable"
  | "not_found"
  | "invalid_input";
export type ManagementErrorCode =
  | ErrorCode
  | "not_signed_in"
  | "other_merchant"
  | "not_built_yet";
export type Problem = {
  code: ProblemCode;
  services?: number[];
  table?: string | null;
  guest?: string | null;
  seats?: number | null;
};
export type ManagementError = {
  error: ManagementErrorCode;
  message: string;
  fallback_url?: string | null;
  problem?: Problem | null;
};

// The demo merchant's data (#131), read by the management mock client:
// code/tests/fixtures/demo/la-graine-verte-management.json. Not an API shape.
export type ManagementFixture = {
  // The demo's clock: its day is "today", and the mock's clock starts there.
  now: string;
  merchant: MerchantOverview;
  hours: Hours;
  tables: TableSpec[];
  rules: Rules;
  settings: MerchantSettings;
  // The 30 days ending today; today's entry matches `reservations`.
  history: DayHistory[];
  // Bookings per channel over `history`.
  channels: ChannelCount[];
  // Every booking of the 7 days starting today.
  reservations: ManagedReservation[];
  blocked: BlockedTime[];
  // Newest first.
  activity: ActivityItem[];
  agent_requests: AgentRequest[];
  // The booking an agent makes a few seconds after the site opens.
  live_booking: LiveEvent;
};

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
  // Profiles stored before #149 have none: the management site uses the default look.
  look?: Look | null;
  // Profiles stored before #248 have none: the merchant's agent says it doesn't know.
  knowledge?: SiteKnowledge | null;
};

export type KnowledgeTopic =
  | "menu"
  | "drinks"
  | "dietary"
  | "access"
  | "practical"
  | "about"
  | "events"
  | "other";

// One thing a guest's agent may ask about. No source: the owner said it (#248).
export type KnowledgeItem = {
  topic: KnowledgeTopic;
  text: string;
  source_url: string | null;
  quote: string | null;
};

export type SiteKnowledge = { items: KnowledgeItem[] };

// The merchant site's colours and shapes, read by code from its homepage (#149).
export type Look = {
  bg: string;
  surface: string;
  line: string;
  ink: string;
  muted: string;
  accent: string;
  accent_2: string;
  on_accent: string;
  display_font: "Fraunces Soft" | "serif" | "sans";
  body_font: "Karla" | "serif" | "sans";
  corners: "organic" | "rounded" | "square";
  radius_px: number;
  texture: "leather" | "none";
};

export type GeneratedTool = { name: string; description: string };

export type StudioView = "agent" | "manage";
// A moment of the restaurant's day: Home shows what the merchant needs then (#154).
export type Moment = "morning" | "before" | "during" | "after";
// The management site's pages a reply can link to (#140): Home and the Assistant aside.
export type ManagePage = "reservations" | "guests" | "hours" | "floor" | "rules" | "agent" | "settings";

// GET /merchants/<slug>/assistant (#140): the owner's conversation, as the chat shows it. The
// session is the platform's, shared by the owner's devices.
export type ConversationItem =
  | { type: "user"; text: string }
  | { type: "assistant"; text: string }
  | { type: "card"; card: Extract<ChatEvent, { type: "card" }> }
  | { type: "notification"; event: LiveEvent }
  | { type: "error"; message: string };
export type AssistantConversation = { session_id: string; items: ConversationItem[] };
export type DiffCardData = {
  label: string;
  before: string | null;
  after: string;
  // The pane "See it" opens in Studio.
  view: StudioView | null;
};

export type FactKey =
  | "name"
  | "hours"
  | "tags"
  | "max_party_size"
  | "cancellation_policy"
  | "price_level"
  | "booking_page"
  | "look"
  | KnowledgeTopic;

// One line of the review card (#136). `source`: a crawled page's path, "you" for the owner's
// description, null for a default.
// Where a fact's quote sits on its page's screenshot, in the image's pixels (#151).
export type FactBox = { page: string; x: number; y: number; width: number; height: number };

export type BuildFact = {
  key: FactKey;
  label: string;
  value: string;
  source: string | null;
  quote: string | null;
  box?: FactBox | null;
};

// Something to check before publishing, written by code (#136).
export type BuildFlag = { title: string; detail: string };

// A crawled page's screenshot, without images or web fonts (#151). `image`: a data: URL.
export type SitePage = { path: string; width: number; height: number; image: string };
// GET /merchants/<slug>/site
export type PublicSite = { pages: SitePage[] };
// POST /merchants/<slug>/agent/try: both frames, and the node's answer (404 until published).
export type AgentIntent = "DISCOVER" | "AVAILABILITY" | "BOOK" | "AMEND" | "CANCEL" | "RECALL" | "PING";
export type AgentTryOutput = {
  request: string;
  response: string;
  status: number;
  // A TALK's 200 answer, read from its frame (#253).
  talk?: TalkOutput | null;
};

// One message to the merchant's agent. `confirm` names a `pending` proposal of the conversation.
export type AgentTalkInput = { message: string; conversation?: string | null; confirm?: string | null };

// A write the agent proposes and won't run before a `confirm` that names it (#247).
export type PendingAction = {
  id: string;
  action: "book" | "change" | "cancel";
  summary: string;
  expires_at: string;
};

// Only `reply` is the model's text; code builds every other field from tool results.
export type TalkOutput = {
  conversation: string;
  reply: string;
  slots: { starts_at: string; table_label: string }[];
  pending: PendingAction | null;
  reservation: { id: string; starts_at: string; party_size: number; table_label: string; status: string; guest_name: string } | null;
  fallback_url: string | null;
  tools_called: string[];
};

export type BuildAgentOutput = {
  profile: MerchantProfile;
  // Hidden from guests until the owner publishes (#136).
  status: "building";
  assumptions: string[];
  facts: BuildFact[];
  flags: BuildFlag[];
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
  // A page the crawl read, as soon as it is done: Studio shows it during the build (#151).
  | { type: "site_page"; page: SitePage }
  | {
      type: "step";
      step: "crawl" | "extract" | "validate" | "provision" | "deploy";
      // `waiting`: `deploy` after a build, until the owner publishes (#136).
      status: "running" | "done" | "error" | "waiting";
      detail?: string | null;
      duration_ms?: number | null;
    }
  // `login_url`: the owner's secret link, shown once after a build (#132).
  | { type: "card"; kind: "profile"; data: BuildAgentOutput; login_url?: string | null }
  | {
      type: "card";
      kind: "reservations";
      data: { date: string; reservations: AdminReservationRow[] };
    }
  | { type: "card"; kind: "stats"; data: Stats & { date: string } }
  | { type: "card"; kind: "policy"; data: Policy }
  | { type: "card"; kind: "confirmation"; data: { text: string } }
  // A change, worded by code (#152): the old value struck through, the new one bold.
  | { type: "card"; kind: "diff"; data: DiffCardData }
  // Studio shows that pane of its canvas; the plain chat shows nothing (#152).
  | { type: "card"; kind: "view"; data: { view: StudioView; moment?: Moment | null } }
  // A link from a reply to a management page, worded by code (#140).
  | { type: "card"; kind: "page"; data: { page: ManagePage } }
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
// core.models. Days are YYYY-MM-DD and times HH:MM, local to the merchant
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
  // The server's clock, in `timezone`: its date is the merchant's today.
  now: string;
  agent_address: string;
  mcp_url: string;
  // Absolute: https://<web host>/manage/<slug>.
  management_url: string;
  // What the owner asked to be called; null until known.
  owner_name: string | null;
  // Built from one of the demo sites (code/sites/).
  demo: boolean;
  // The site's look (#149), for the management site's tokens (#150); null: the default look.
  look?: Look | null;
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

// GET /merchants/<slug>/dashboard: the merchant's today by the server's clock.
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
  | "needs_invite"
  | "not_built_yet"
  | "too_many_requests";
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

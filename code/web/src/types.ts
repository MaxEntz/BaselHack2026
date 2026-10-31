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

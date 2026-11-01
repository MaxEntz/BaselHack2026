// The forms' routes (#133): opening hours, floor plan, booking rules and settings. Each answers
// the `Problem` of the rule it broke, and logs a line only when something changed.
import type { ManagementClient } from "../../management/client.ts";
import { toMinutes } from "../../management/services.ts";
import type {
  FloorPlanInput,
  GeneratedTool,
  HoursInput,
  MerchantSettings,
  Rules,
  RulesInput,
  Service,
  SettingsInput,
  TableSpec,
  Tag,
} from "../../types.ts";
import { services } from "./bookings.ts";
import { type Mock, invalid, log, problem, respond } from "./context.ts";
import {
  HHMM,
  MAX_POLICY_TEXT,
  MAX_SEATS,
  MAX_SERVICES,
  MAX_SHORT_TEXT,
  exact,
  parseUrl,
  sameSite,
  text,
} from "./input.ts";
import { checkedPlan } from "./tables.ts";

// `core.models`: a service lasts at least an hour.
const SHORTEST_SERVICE_MIN = 60;
const TAGS: readonly Tag[] = [
  "gluten-free",
  "vegetarian",
  "vegan",
  "halal",
  "kosher",
  "terrace",
  "kid-friendly",
];
const PRICES = ["€", "€€", "€€€", "€€€€"];
// gateway/merchant_server.py: an AI agent never reads the merchant's own policy.
const POLICY_ELSEWHERE = "see the restaurant's own website";

type FormRoutes = Pick<
  ManagementClient,
  "hours" | "saveHours" | "tables" | "saveTables" | "rules" | "saveRules" | "settings" | "saveSettings"
>;

export function formRoutes(mock: Mock): FormRoutes {
  return {
    hours: () => respond(mock, { services: services(mock) }),
    saveHours: async (_slug, body) => respond(mock, { services: saveHours(mock, body) }),
    tables: () => respond(mock, { tables: mock.data.tables }),
    saveTables: async (_slug, body) => respond(mock, { tables: saveTables(mock, body) }),
    rules: () => respond(mock, mock.data.rules),
    saveRules: async (_slug, body) => respond(mock, saveRules(mock, body)),
    settings: () => respond(mock, mock.data.settings),
    saveSettings: async (_slug, body) => respond(mock, saveSettings(mock, body)),
  };
}

function saveHours(mock: Mock, body: HoursInput): Service[] {
  exact(body, ["services"]);
  if (body.services.length > MAX_SERVICES) invalid(`services: at most ${MAX_SERVICES}`);
  // DraftTime (`core.models`): blank is missing, else HH:MM, before any rule.
  const drafts = body.services.map((draft, index) => {
    exact(draft, ["start", "end"]);
    const time = (value: unknown) => {
      if (value === null || value === undefined) return null;
      if (typeof value !== "string") return invalid(`services.${index}: HH:MM times`);
      if (!value.trim()) return null;
      return HHMM.test(value) ? value : invalid(`services.${index}: HH:MM times`);
    };
    return { start: time(draft.start), end: time(draft.end) };
  });
  if (drafts.length === 0) problem("Keep at least one service.", { code: "no_services" });
  const saved = drafts.map(({ start, end }, index): Service => {
    if (start === null || end === null) {
      return problem("A service needs a start and an end.", {
        code: "service_incomplete",
        services: [index],
      });
    }
    if (toMinutes(end) - toMinutes(start) < SHORTEST_SERVICE_MIN) {
      problem("A service lasts at least 60 minutes.", { code: "service_too_short", services: [index] });
    }
    return { start, end };
  });
  saved.forEach((a, i) =>
    saved.forEach((b, j) => {
      if (i < j && a.start < b.end && b.start < a.end) {
        problem("Services must not overlap.", { code: "services_overlap", services: [i, j] });
      }
    }),
  );
  // Stored by start, as the server does: the same hours in another order are no change.
  const byStart = saved.toSorted((a, b) => a.start.localeCompare(b.start));
  const before = services(mock);
  const changed =
    byStart.length !== before.length ||
    byStart.some((s, i) => s.start !== before[i]?.start || s.end !== before[i]?.end);
  mock.data = { ...mock.data, hours: { services: byStart } };
  withTools(mock);
  if (changed) log(mock, "saved", "Opening hours", "merchant");
  return services(mock);
}

function saveTables(mock: Mock, body: FloorPlanInput): TableSpec[] {
  const tables = checkedPlan(mock, body);
  const plan = (rows: readonly TableSpec[]) =>
    JSON.stringify(rows.map((t) => [t.label, t.seats]).toSorted());
  const changed = plan(tables) !== plan(mock.data.tables);
  mock.data = { ...mock.data, tables };
  if (changed) log(mock, "saved", "Floor plan", "merchant");
  return mock.data.tables;
}

function saveRules(mock: Mock, body: RulesInput): Rules {
  exact(body, ["max_party_size", "fallback_url", "cancellation_policy"]);
  const largest = body.max_party_size;
  if (!Number.isInteger(largest)) invalid("max_party_size: an integer is required");
  const page = parseUrl(body.fallback_url) ?? invalid("fallback_url: a URL is required");
  if (page.protocol !== "http:" && page.protocol !== "https:") {
    invalid("fallback_url: an http(s) URL is required");
  }
  const policy = text(body.cancellation_policy, "cancellation_policy", MAX_POLICY_TEXT, false);
  if (largest < 1) problem("The largest group is at least 1.", { code: "group_too_small" });
  if (largest > MAX_SEATS) {
    problem(`The largest group is at most ${MAX_SEATS}.`, { code: "too_many_seats", seats: MAX_SEATS });
  }
  if (!sameSite(page, mock.data.merchant.website)) {
    problem("The booking page stays on your website.", { code: "booking_page_off_site" });
  }
  const changes = {
    max_party_size: largest,
    fallback_url: page.href, // as HttpUrl normalises it
    cancellation_policy: policy,
  };
  const before = mock.data.rules;
  const changed =
    changes.max_party_size !== before.max_party_size ||
    changes.fallback_url !== before.fallback_url ||
    changes.cancellation_policy !== before.cancellation_policy;
  mock.data = { ...mock.data, rules: { ...mock.data.rules, ...changes } };
  withTools(mock);
  if (changed) log(mock, "saved", "Booking rules", "merchant");
  return mock.data.rules;
}

function saveSettings(mock: Mock, body: SettingsInput): MerchantSettings {
  exact(body, ["name", "city", "price_level", "tags"]);
  if (typeof body.name !== "string") invalid("name: a string is required");
  const name = text(body.name, "name", MAX_SHORT_TEXT, false);
  const city = text(body.city, "city", MAX_SHORT_TEXT, false);
  if (!PRICES.includes(body.price_level)) invalid("price_level: one of €, €€, €€€, €€€€");
  if (body.tags.length > TAGS.length) invalid(`tags: at most ${TAGS.length}`);
  if (body.tags.some((tag) => !TAGS.includes(tag))) invalid("tags: unknown tag");
  if (name === null) return problem("The restaurant needs a name.", { code: "name_required" });
  const changes = { name, city, price_level: body.price_level, tags: [...new Set(body.tags)] };
  const before = mock.data.settings;
  const changed =
    name !== before.name ||
    city !== before.city ||
    changes.price_level !== before.price_level ||
    changes.tags.join() !== before.tags.join();
  mock.data = {
    ...mock.data,
    settings: { ...mock.data.settings, ...changes },
    merchant: { ...mock.data.merchant, ...changes },
  };
  withTools(mock);
  if (changed) log(mock, "saved", "Settings", "merchant");
  return mock.data.settings;
}

function withTools(mock: Mock): void {
  mock.data = { ...mock.data, rules: { ...mock.data.rules, tools: tools(mock) } };
}

// What an AI agent reads first on /m/<slug>/mcp (agent/templates/restaurant.json).
function tools(mock: Mock): GeneratedTool[] {
  const { name, city } = mock.data.settings;
  const hours = services(mock)
    .map((s) => `${s.start}-${s.end}`)
    .join(", ");
  const groups = mock.data.rules.max_party_size;
  return [
    {
      name: "get_availability",
      description: `Check free tables at ${name} (${city ?? "unknown city"}). Services: ${hours}. Groups up to ${groups}.`,
    },
    {
      name: "create_reservation",
      description: `Book a table at ${name}. Always confirm with the guest first.`,
    },
    { name: "update_reservation", description: `Move or resize a booking at ${name}.` },
    {
      name: "cancel_reservation",
      description: `Cancel a booking at ${name}. Policy: ${POLICY_ELSEWHERE}.`,
    },
  ];
}

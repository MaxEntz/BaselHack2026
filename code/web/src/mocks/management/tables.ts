// A floor plan as `update_tables` checks it (agent/admin.py), before it replaces the tables.
import type { FloorPlanInput, TableSpec } from "../../types.ts";
import { type Mock, invalid, problem } from "./context.ts";
import { MAX_SEATS, MAX_TABLE_LABEL, MAX_TABLES, exact, required } from "./input.ts";

// What Python's str.isprintable refuses, which agent/admin.py asks of a table label.
const UNPRINTABLE = /(?! )[\p{C}\p{Z}]/u;

/** The plan's tables, or the refusal of the first rule it breaks. */
export function checkedPlan(mock: Mock, body: FloorPlanInput): TableSpec[] {
  exact(body, ["tables"]);
  if (body.tables.length > MAX_TABLES) invalid(`tables: at most ${MAX_TABLES}`);
  const tables = body.tables.map((draft) => {
    exact(draft, ["label", "seats"]);
    const label = required(draft.label, "label", MAX_TABLE_LABEL);
    if (!Number.isInteger(draft.seats)) invalid("seats: an integer is required");
    return { label, seats: draft.seats };
  });
  if (tables.length === 0) problem("Keep at least one table.", { code: "no_tables" });
  checkSeats(tables);
  const unprintable = tables.find((table) => UNPRINTABLE.test(table.label));
  if (unprintable) {
    const label = unprintable.label;
    invalid(`Invalid table '${label}': printable label and at most ${MAX_SEATS} seats`);
  }
  checkPlanChange(mock, tables);
  return tables;
}

function checkSeats(tables: readonly TableSpec[]): void {
  const labels = new Set<string>();
  for (const { label, seats } of tables) {
    if (labels.has(label)) {
      problem("Table labels must be unique.", { code: "duplicate_table", table: label });
    }
    labels.add(label);
    if (seats < 1) {
      problem("Every table needs at least 1 seat.", { code: "table_without_seats", table: label });
    }
    if (seats > MAX_SEATS) {
      problem(`A table has at most ${MAX_SEATS} seats.`, {
        code: "too_many_seats",
        table: label,
        seats: MAX_SEATS,
      });
    }
  }
}

// agent/admin.py `_check_plan_change`: of the bookings at a removed or shrunk table, by start,
// the first one still running that the plan strands; else a removed table that past or
// cancelled bookings refer to.
function checkPlanChange(mock: Mock, plan: readonly TableSpec[]): void {
  const wanted = new Map(plan.map((table) => [table.label, table.seats]));
  const affected = new Set(
    mock.data.tables.filter((t) => (wanted.get(t.label) ?? 0) < t.seats).map((t) => t.label),
  );
  const bookings = mock.data.reservations
    .filter((b) => affected.has(b.table_label))
    .toSorted(
      (a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`) || (a.id < b.id ? -1 : 1),
    );
  const history = new Map<string, string>();
  for (const booking of bookings) {
    const { table_label: table, guest_name: guest } = booking;
    const seats = wanted.get(table);
    const running = booking.status === "confirmed" && !mock.hasEnded(booking);
    if (seats === undefined) {
      if (running) {
        problem("A booking refers to this table.", { code: "table_in_use", table, guest });
      }
      if (!history.has(table)) history.set(table, guest);
    } else if (running && booking.party_size > seats) {
      problem("A booking needs more seats at this table.", {
        code: "table_too_small",
        table,
        guest,
        seats: booking.party_size,
      });
    }
  }
  const first = [...history.keys()].toSorted()[0];
  if (first !== undefined) {
    problem("Past bookings refer to this table.", {
      code: "table_in_use",
      table: first,
      guest: history.get(first),
    });
  }
}

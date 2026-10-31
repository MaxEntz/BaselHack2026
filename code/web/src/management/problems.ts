import type { Problem } from "../types.ts";
import { ManagementRequestError } from "./client.ts";

/**
 * A form rule the server refused, in the merchant's words (#133). `services` names the
 * submitted services, by index.
 */
export function problemMessage(problem: Problem, services: readonly string[] = []): string {
  const [first, second] = (problem.services ?? []).map((index) => services[index] ?? "A service");
  const table = problem.table ?? "This table";
  const guest = problem.guest ?? "a guest";
  switch (problem.code) {
    case "no_services":
      return "Keep at least one service.";
    case "service_incomplete":
      return `Give ${first} a start and an end.`;
    case "service_too_short":
      return `${first} has to last at least an hour.`;
    case "services_overlap":
      return `${first} and ${second} overlap.`;
    case "no_tables":
      return "Keep at least one table.";
    case "duplicate_table":
      return `There are two tables called ${table}.`;
    case "table_without_seats":
      return "Every table needs at least 1 seat.";
    case "too_many_seats":
      return problem.table
        ? `${table} can have at most ${problem.seats} seats.`
        : `The largest group can be at most ${problem.seats}.`;
    case "table_in_use":
      return `${table} has to stay, because ${guest}'s booking refers to it.`;
    case "table_too_small":
      return `${table} needs ${problem.seats} seats, because of ${guest}'s booking.`;
    case "group_too_small":
      return "The largest group has to be at least 1.";
    case "name_required":
      return "Your restaurant needs a name.";
    case "booking_page_off_site":
      return "Your booking page has to be on your own website.";
  }
}

/** What to show when a call fails: the broken rule, else the platform's message. */
export function errorMessage(error: unknown, services: readonly string[] = []): string {
  if (error instanceof ManagementRequestError) {
    return error.body.problem ? problemMessage(error.body.problem, services) : error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

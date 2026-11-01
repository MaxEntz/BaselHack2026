import type { ManagementClient } from "../management/client.ts";
import type { ManagementFixture } from "../types.ts";
import { agentRoutes } from "./management/agent.ts";
import { type MockOptions, createMock } from "./management/context.ts";
import { formRoutes } from "./management/forms.ts";
import { homeRoutes } from "./management/home.ts";
import { liveRoutes } from "./management/live.ts";
import { reservationRoutes } from "./management/reservations.ts";

// The management site against the demo fixture, with no platform (VITE_MOCK=1).
// It applies the server's rules (#133, #134), not the mockup's: past times are
// refused, bodies hold only their input's fields, forms answer the `Problem`.
// The clock starts at `fixture.now` and runs. Each route group lives in
// `management/`, on the one state `createMock` makes.
export function createMockManagementClient(
  fixture: ManagementFixture,
  options: MockOptions = {},
): ManagementClient {
  const mock = createMock(fixture, options);
  return {
    ...agentRoutes(mock),
    ...homeRoutes(mock),
    ...reservationRoutes(mock),
    ...formRoutes(mock),
    ...liveRoutes(mock),
  };
}

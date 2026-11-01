import type { ConversationItem, ManagementFixture } from "../types.ts";
import { httpManagementClient, type ManagementClient } from "./client.ts";

// VITE_MOCK=1: the demo fixture (#131) instead of the platform. The mock and the fixture are
// imported only then, so that a production build drops them (#220). `npm run check:contracts`
// checks the fixture against `ManagementFixture`.
export const managementClient: ManagementClient =
  import.meta.env.VITE_MOCK === "1" ? await demoClient() : httpManagementClient;

async function demoClient(): Promise<ManagementClient> {
  const [{ createMockManagementClient }, { MOCK_SITE_PAGE }, fixture, opening] = await Promise.all([
    import("../mocks/managementClient.ts"),
    import("../mocks/site.ts"),
    import("../../../tests/fixtures/demo/la-graine-verte-management.json"),
    import("../../../tests/fixtures/demo/la-graine-verte-assistant.json"),
  ]);
  return createMockManagementClient(fixture.default as ManagementFixture, {
    conversation: opening.default.opening as ConversationItem[],
    sitePages: [MOCK_SITE_PAGE],
  });
}

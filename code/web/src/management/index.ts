import fixture from "../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { createMockManagementClient } from "../mocks/managementClient.ts";
import { MOCK_SITE_PAGE } from "../mocks/site.ts";
import type { ManagementFixture } from "../types.ts";
import { httpManagementClient, type ManagementClient } from "./client.ts";

// VITE_MOCK=1: the demo fixture (#131) instead of the platform. `npm run
// check:contracts` checks the fixture against `ManagementFixture`.
export const managementClient: ManagementClient =
  import.meta.env.VITE_MOCK === "1"
    ? createMockManagementClient(fixture as ManagementFixture, { sitePages: [MOCK_SITE_PAGE] })
    : httpManagementClient;

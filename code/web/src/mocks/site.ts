import type { SitePage } from "../types.ts";
import image from "./la-graine-verte-site.jpg";
import size from "./la-graine-verte-site.json";

// La Graine Verte's demo site, through the real crawl (#151): what `site_page` and GET /site send.
export const MOCK_SITE_PAGE: SitePage = { ...size, image };

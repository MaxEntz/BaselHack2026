// The shell's wording (sesame-management.html, top bar and sidebar, #137).

import type { ActivityItem, PriceLevel } from "../../types.ts";
import { KIND, clockOf } from "../pages/home/format.ts";
import { PAGES, type PageId } from "../pages.ts";

/** "Sat 31 Oct, 19:40": `at` in the merchant's `timeZone`. */
export function clockLabel(at: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("weekday")} ${part("day")} ${part("month")}, ${part("hour")}:${part("minute")}`;
}

/** "Mulhouse, €", or the price alone without a city. */
export function brandLine(city: string | null, price: PriceLevel): string {
  return city ? `${city}, ${price}` : price;
}

/** "Camille Lang" → "CL", "Camille" → "C": the first letters of the first two words. */
export function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => [...word][0]?.toUpperCase() ?? "")
    .join("");
}

/** The search shortcut as the keyboard shows it: ⌘K on Apple devices, Ctrl K elsewhere. */
export function shortcutLabel(platform: string): string {
  return /mac|iphone|ipad|ipod/i.test(platform) ? "⌘K" : "Ctrl K";
}

/** The browser's platform, for `shortcutLabel`. */
export function browserPlatform(): string {
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  return nav.userAgentData?.platform || nav.platform || "";
}

/** "19:40, blocked: 13:00, tomorrow": one line of the bell's popover. */
export function notificationLine(item: ActivityItem): string {
  return `${clockOf(item.at)}, ${KIND[item.kind].toLowerCase()}: ${item.text}`;
}

/** The pages whose name holds `query`, in the sidebar's order. */
export function pageHits(query: string): PageId[] {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return [];
  return (Object.keys(PAGES) as PageId[]).filter((page) => PAGES[page].toLowerCase().includes(wanted));
}

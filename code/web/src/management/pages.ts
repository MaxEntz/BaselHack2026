// The management site's pages, in the sidebar's order (sesame-management.html#<page>).

export const PAGES = {
  home: "Home",
  assistant: "Assistant",
  reservations: "Reservations",
  guests: "Guests",
  hours: "Opening hours",
  floor: "Floor plan",
  rules: "Booking rules",
  agent: "Agent",
  settings: "Settings",
} as const;

export type PageId = keyof typeof PAGES;

export function isPage(value: string | undefined): value is PageId {
  return value !== undefined && Object.hasOwn(PAGES, value);
}

/** Page title: `{name} · {Page}`. */
export function pageTitle(name: string, page: PageId): string {
  return `${name} · ${PAGES[page]}`;
}

/**
 * The merchant's display name until the management API (#129) gives the real one:
 * the slug in title case, "la-graine-verte" → "La Graine Verte".
 */
export function nameFromSlug(slug: string): string {
  return slug
    .split("-")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/** A page of `slug`'s site: `/manage/<slug>`, or `/manage/<slug>/<page>`. */
export function pagePath(slug: string, page: PageId): string {
  const base = `/manage/${encodeURIComponent(slug)}`;
  return page === "home" ? base : `${base}/${page}`;
}

/** A booking, opened on the Reservations page of its day (#139). */
export function bookingPath(slug: string, date: string, id: string): string {
  return `${pagePath(slug, "reservations")}?date=${date}&booking=${encodeURIComponent(id)}`;
}

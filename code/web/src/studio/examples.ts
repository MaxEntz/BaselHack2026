// The fictional demo restaurants, offered in demo mode only: VITE_MOCK, or VITE_DEMO=1 on a
// platform that can read their sites. VITE_DEMO_SITES_DOMAIN gives their host, as
// <slug>.<domain>; without it they are the `make sites` ports.
export type Example = {
  slug: string;
  name: string;
  address: string;
  // What the merchant says about how they work.
  brief: string;
  colour: string;
  ink: string;
};

const env = import.meta.env;
const MOCK = env.VITE_MOCK === "1";
export const DEMO = MOCK || env.VITE_DEMO === "1";

function address(slug: string, host: string, port: number): string {
  if (MOCK) return host;
  const domain = env.VITE_DEMO_SITES_DOMAIN;
  return domain ? `https://${slug}.${domain}` : `localhost:${port}`;
}

export const EXAMPLES: readonly Example[] = DEMO
  ? [
      {
        slug: "le-petit-zinc",
        name: "Le Petit Zinc",
        address: address("le-petit-zinc", "lepetitzinc.example", 8080),
        brief: "44 seats on 12 tables. Groups up to 6. Open every day.",
        colour: "#8f2d2a",
        ink: "#fff8ee",
      },
      {
        slug: "la-graine-verte",
        name: "La Graine Verte",
        address: address("la-graine-verte", "lagraineverte.example", 8081),
        brief: "24 seats, 6 tables of 2 and 3 tables of 4. Lunch is the busy one.",
        // Olive, not the mockup's green: this restaurant's look is olive and brick (#127). The
        // shade its management site draws, where white text reads (tokens.css, #153).
        colour: "#616c48",
        ink: "#f4f2ea",
      },
      {
        slug: "maison-albane",
        name: "Maison Albane",
        address: address("maison-albane", "maisonalbane.example", 8082),
        brief: "22 seats, 5 tables of 2 and 3 tables of 4. One service each evening.",
        colour: "#c8a96a",
        ink: "#151413",
      },
    ]
  : [];

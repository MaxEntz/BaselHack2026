interface ImportMetaEnv {
  // "1" replays src/mocks/*.json instead of calling the platform API.
  readonly VITE_MOCK?: string;
  // Skip onboarding and connect live events for an existing merchant.
  readonly VITE_MERCHANT_SLUG?: string;
  readonly VITE_MERCHANT_NAME?: string;
  // "1" offers the demo restaurants in Studio.
  readonly VITE_DEMO?: string;
  // Their sites' domain, as <slug>.<domain>; without it, the `make sites` ports.
  readonly VITE_DEMO_SITES_DOMAIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

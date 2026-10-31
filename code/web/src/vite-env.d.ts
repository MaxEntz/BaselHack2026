interface ImportMetaEnv {
  // "1" replays src/mocks/*.json instead of calling the platform API.
  readonly VITE_MOCK?: string;
  // Skip onboarding and connect live events for an existing merchant.
  readonly VITE_MERCHANT_SLUG?: string;
  readonly VITE_MERCHANT_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

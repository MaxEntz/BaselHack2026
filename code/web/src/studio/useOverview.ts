import { useCallback, useEffect, useState } from "react";
import { merchantMcpUrl } from "../cards/format.ts";
import type { AgentStatus } from "../chat/state.ts";
import { managementClient } from "../management/index.ts";
import type { MerchantOverview, Service, SitePage } from "../types.ts";
import type { Addresses } from "./feed.tsx";

type OverviewInput = {
  slug: string | null;
  // False while the build's owner cookie is on its way (#132).
  claimed: boolean;
  // A merchant found again after a reload, with no profile card in the conversation.
  restored: boolean;
  status: AgentStatus | null;
  setStatus: (status: AgentStatus) => void;
};

export type Overview = {
  overview: MerchantOverview | null;
  // What a restored merchant's build stored: the pages it read and its hours.
  pages: readonly SitePage[];
  services: readonly Service[];
  // A new conversation: none of it belongs to the next merchant.
  forget: () => void;
};

/** What the platform knows of the merchant once the owner cookie is set. */
export function useOverview({ slug, claimed, restored, status, setStatus }: OverviewInput): Overview {
  const [overview, setOverview] = useState<MerchantOverview | null>(null);
  const [pages, setPages] = useState<readonly SitePage[]>([]);
  const [services, setServices] = useState<readonly Service[]>([]);

  // The addresses, and a merchant found again after a reload: is it live or still hidden?
  // Read again when the status changes.
  useEffect(() => {
    if (!slug || !claimed) return;
    const abort = new AbortController();
    managementClient
      .merchant(slug, abort.signal)
      .then((merchant) => {
        if (abort.signal.aborted) return;
        setOverview(merchant);
        if (restored) setStatus(merchant.status);
      })
      .catch(() => {
        // Not signed in, or the platform is away: the card falls back to what the build said.
      });
    return () => abort.abort();
  }, [slug, claimed, restored, status, setStatus]);

  // A merchant found again after a reload: the pages stored with its build.
  useEffect(() => {
    if (!restored || !slug || !claimed) return;
    const abort = new AbortController();
    managementClient
      .site(slug, abort.signal)
      .then((site) => {
        if (!abort.signal.aborted) setPages(site.pages);
      })
      .catch(() => {
        // The public tab stays disabled.
      });
    managementClient
      .hours(slug, abort.signal)
      .then((hours) => {
        if (!abort.signal.aborted) setServices(hours.services);
      })
      .catch(() => {
        // The dock then names its moments instead of their times.
      });
    return () => abort.abort();
  }, [restored, slug, claimed]);

  const forget = useCallback(() => {
    setOverview(null);
    setPages([]);
    setServices([]);
  }, []);

  return { overview, pages, services, forget };
}

/** From the platform once the owner cookie is set; until then, what the build said. */
export function addressesOf(
  slug: string | null,
  overview: MerchantOverview | null,
  mcpUrl: string | undefined,
): Addresses | null {
  if (slug && overview?.slug === slug) {
    return { agent: overview.agent_address, mcp: overview.mcp_url, management: overview.management_url };
  }
  if (!slug || !mcpUrl) return null;
  const { origin, hostname } = window.location;
  return {
    agent: `batp://${hostname}/${slug}`,
    mcp: merchantMcpUrl(mcpUrl, slug),
    management: `${origin}/manage/${slug}`,
  };
}

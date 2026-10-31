import { useState } from "react";
import type { ReactNode } from "react";
import type { BuildAgentOutput } from "../types.ts";
import { formatServices, plural } from "./format.ts";

export function ProfileCard({ data }: { data: BuildAgentOutput }) {
  const { profile, assumptions, tools, mcp_url } = data;
  // The tables are the source of truth for capacity (documentation/CONTRACTS.md).
  const seats = profile.tables.reduce((sum, table) => sum + table.seats, 0);
  const subtitle = [profile.location?.city, profile.price_level]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className="card profile-card" aria-label="Agent profile">
      <header className="profile-header">
        <div>
          <h2 className="card-title">{profile.name}</h2>
          <p className="muted">
            {subtitle && `${subtitle} · `}
            <ExternalLink href={profile.website} />
          </p>
        </div>
        <span className="pill pill-live">Live — discoverable by AI assistants</span>
      </header>

      {profile.tags.length > 0 && (
        <ul className="chips" aria-label="Tags">
          {profile.tags.map((tag) => (
            <li key={tag} className="chip">
              {tag}
            </li>
          ))}
        </ul>
      )}

      <dl className="facts">
        <Fact label="Services">{formatServices(profile.services)}</Fact>
        <Fact label="Floor plan">
          {plural(profile.tables.length, "table")} · {plural(seats, "seat")}
        </Fact>
        <Fact label="Max party size">{plural(profile.max_party_size, "guest")}</Fact>
        <Fact label="Cancellation">
          {profile.cancellation_policy ?? "No policy specified"}
        </Fact>
        <Fact label="Fallback">
          <ExternalLink href={profile.fallback_url} />
        </Fact>
      </dl>

      {assumptions.length > 0 && (
        <div className="assumptions" role="note">
          <strong>Assumed (not found on your site) — tell me if that's wrong:</strong>
          <ul>
            {assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="card-subtitle">Your agent's tools</h3>
      <ul className="tools">
        {tools.map((tool) => (
          <li key={tool.name}>
            <code>{tool.name}</code>
            <span>{tool.description}</span>
          </li>
        ))}
      </ul>

      <McpUrl label="MCP URL (all restaurants)" url={mcp_url} />
      <McpUrl label="This restaurant only" url={merchantMcpUrl(mcp_url, profile.slug)} />
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function ExternalLink({ href }: { href: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {href.replace(/^https?:\/\//, "").replace(/\/$/, "")}
    </a>
  );
}

// The gateway also serves each restaurant on its own: /m/<slug>/mcp next to /mcp (#23).
function merchantMcpUrl(mcpUrl: string, slug: string): string {
  return mcpUrl.replace(/\/mcp$/, `/m/${slug}/mcp`);
}

function McpUrl({ label, url }: { label: string; url: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard needs a secure context; the URL stays selectable.
    }
  }

  return (
    <div className="mcp-url">
      <span className="muted">{label}</span>
      <code>{url}</code>
      <button type="button" className="button-secondary" onClick={() => void copy()}>
        {copied ? "Copied ✓" : "Copy"}
      </button>
    </div>
  );
}

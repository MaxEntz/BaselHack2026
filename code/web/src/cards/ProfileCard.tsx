import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { copyText } from "../clipboard.ts";
import type { BuildAgentOutput } from "../types.ts";
import { strings } from "../strings.ts";
import { merchantMcpUrl, plural } from "./format.ts";

// How long a copy button says "Copied".
const COPIED_MS = 2000;

type ProfileCardProps = { data: BuildAgentOutput; loginUrl?: string | null };

export function ProfileCard({ data, loginUrl = null }: ProfileCardProps) {
  const { profile, facts, flags, status, tools, mcp_url } = data;
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
        <span className={status === "building" ? "pill" : "pill pill-live"}>
          {status === "building" ? strings.profileHidden : strings.profileLive}
        </span>
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
        {facts.map((fact) => (
          <Fact key={fact.key} label={fact.label} source={fact.source}>
            {fact.value}
          </Fact>
        ))}
        <Fact label="Floor plan" source={null}>
          {plural(profile.tables.length, "table")} · {plural(seats, "seat")}
        </Fact>
      </dl>

      {flags.length > 0 && (
        <div className="assumptions" role="note" aria-label="Check these">
          <strong>Check these before you publish:</strong>
          <ul>
            {flags.map((flag) => (
              <li key={flag.title}>
                <b>{flag.title}</b> {flag.detail}
              </li>
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

      {loginUrl && (
        <div className="assumptions" role="note">
          <strong>Your private link: keep it, it is shown only once.</strong>
          <p>It opens your management site from any browser. Anyone with it can manage your bookings.</p>
          <McpUrl label="Private link" url={loginUrl} />
        </div>
      )}
    </section>
  );
}

type FactProps = { label: string; source: string | null; children: ReactNode };

// `source`: the page the value was read on, "you" for the owner's description (#136).
function Fact({ label, source, children }: FactProps) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>
        {children}
        {source && <em className="fact-source"> {source === "you" ? "you said so" : source}</em>}
      </dd>
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

function McpUrl({ label, url }: { label: string; url: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copy() {
    // Without a clipboard, the URL stays selectable next to the button.
    if ((await copyText(url)).copied) setCopied(true);
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

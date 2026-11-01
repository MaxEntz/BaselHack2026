import { forwardRef, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import type { ManagementClient } from "../management/client.ts";
import { MOMENTS, previewTime } from "../moments.ts";
import type { BuildFact, GeneratedTool, Look, Moment, Service, SitePage } from "../types.ts";
import { AgentSheet } from "./AgentSheet.tsx";
import type { Addresses, View } from "./feed.tsx";
import { type Fit, type Space, fitOf } from "./phase.ts";

const NARROW = 900;
const NAME_ROW = 28;
const STACKED_MANAGE_HEIGHT = 900;
const MIN_MANAGE_HEIGHT = 560;

const NAMES = {
  agent: "Agent site, no interface",
  manage: "Management site",
  public: "Your public site, as read",
};

const pixels = (value: string) => parseFloat(value) || 0;

// The room inside the pasteboard, without its padding.
function useSpace(paste: RefObject<HTMLElement | null>): Space {
  const [space, setSpace] = useState<Space>({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const element = paste.current;
    if (!element) return;
    const measure = () => {
      if (element.clientWidth === 0) return;
      const style = getComputedStyle(element);
      setSpace({
        width: element.clientWidth - pixels(style.paddingLeft) - pixels(style.paddingRight),
        height: element.clientHeight - pixels(style.paddingTop) - pixels(style.paddingBottom),
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [paste]);
  return space;
}

type FrameProps = {
  name: string;
  fit: Fit;
  hidden?: boolean;
  // Fixed height in the frame's own pixels; otherwise it follows its content.
  height?: number;
  children: ReactNode;
};

// A page drawn at its real width and scaled to fit, in a wrapper of the scaled size.
function Frame({ name, fit, hidden = false, height, children }: FrameProps) {
  const inner = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(0);
  useLayoutEffect(() => {
    const element = inner.current;
    if (!element || height !== undefined) return;
    const measure = () => setMeasured(element.offsetHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [height]);
  const shown = height ?? measured;
  return (
    <div className="st-frame" hidden={hidden} style={{ width: fit.width * fit.scale }}>
      <div className="st-frame-name">{name}</div>
      <div className="st-bw" style={{ width: fit.width * fit.scale, height: shown * fit.scale }}>
        <div
          ref={inner}
          className="st-b"
          style={{
            width: fit.width,
            height,
            transform: fit.scale === 1 ? undefined : `scale(${fit.scale})`,
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

type PublicSheetProps = {
  pages: readonly SitePage[];
  fact: BuildFact | null;
  width: number;
  onReveal: (top: number) => void;
};

// The page as the crawl read it, and the fact's words outlined on it.
export function PublicSheet({ pages, fact, width, onReveal }: PublicSheetProps) {
  const [chosen, setChosen] = useState<string | null>(null);
  const wanted = fact?.box?.page ?? chosen;
  const page = pages.find((item) => item.path === wanted) ?? pages[0];
  const ratio = page ? width / page.width : 1;
  const box = fact?.box && fact.box.page === page?.path ? fact.box : null;
  const top = box ? box.y * ratio : null;
  useEffect(() => {
    if (top !== null) onReveal(top);
  }, [top, onReveal]);
  if (!page) return null;
  return (
    <div className="st-public">
      {pages.length > 1 && (
        <div className="st-public-pages" role="group" aria-label="Pages read">
          {pages.map((item) => (
            <button
              key={item.path}
              type="button"
              className="st-chip"
              aria-pressed={item.path === page.path}
              onClick={() => setChosen(item.path)}
            >
              {item.path}
            </button>
          ))}
        </div>
      )}
      <div className="st-public-page">
        <img src={page.image} alt={`${page.path} as read`} style={{ width, height: page.height * ratio }} />
        {box && fact && (
          <div
            className="st-sel"
            data-tag={fact.label}
            style={{
              left: box.x * ratio,
              top: box.y * ratio,
              width: box.width * ratio,
              height: box.height * ratio,
            }}
          />
        )}
      </div>
      <p className="st-public-note">Images and web fonts aren't loaded when your site is read, so photos don't show here.</p>
    </div>
  );
}

const FONT_NAMES: Record<Look["display_font"], string> = {
  "Fraunces Soft": "Fraunces",
  serif: "Serif",
  sans: "Sans",
};

type DockProps = Pick<CanvasProps, "moment" | "services" | "look" | "onMoment" | "onColour">;

// The management site's look and its moments of the day (#154).
function Dock({ moment, services, look, onMoment, onColour }: DockProps) {
  const dots = look ? [look.accent, look.bg, look.accent_2, look.surface] : [];
  return (
    <div className="st-dock">
      {/* One target for the four colours, big enough to tap (WCAG 2.5.8, #153). */}
      {dots.length > 0 && (
        <button type="button" className="st-dots" aria-label="Change the colours" onClick={onColour}>
          {dots.map((colour, index) => (
            <i key={`${colour}-${index}`} style={{ background: colour }} />
          ))}
        </button>
      )}
      {look && <span className="st-dock-font">{FONT_NAMES[look.display_font]}</span>}
      {look && <span className="st-dock-sep" />}
      <div className="st-moments" role="group" aria-label="Time of day">
        {MOMENTS.map(({ id, label }) => {
          const time = previewTime(id, services);
          // The name starts with the time shown, for voice control (WCAG 2.5.3, #153).
          return (
            <button
              key={id}
              type="button"
              title={label}
              aria-label={time ? `${time}, ${label}` : label}
              aria-pressed={moment === id}
              onClick={() => onMoment(id)}
            >
              {time ?? label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

type CanvasProps = {
  client: Pick<ManagementClient, "agentRequests" | "slots" | "tryAgent">;
  // Null until the site is built: only the public tab has something to show.
  slug: string | null;
  live: boolean;
  today: string | null;
  // Waits for the owner cookie, or the page behind it answers 401.
  ready: boolean;
  view: View;
  addresses: Addresses | null;
  pages: readonly SitePage[];
  fact: BuildFact | null;
  tools: readonly GeneratedTool[];
  fallback: string | null;
  onView: (view: View) => void;
  // The management site's moment of the day, from the dock or the chat (#154).
  moment: Moment | null;
  services: readonly Service[];
  look: Look | null;
  onMoment: (moment: Moment) => void;
  onColour: () => void;
};

// The gray pasteboard next to the chat.
export const Canvas = forwardRef<HTMLDivElement, CanvasProps>(function Canvas(
  { client, slug, live, today, ready, view, addresses, pages, fact, tools, fallback, onView, moment, services, look, onMoment, onColour },
  ref,
) {
  const paste = useRef<HTMLDivElement>(null);
  const space = useSpace(paste);
  const narrow = window.innerWidth <= NARROW;
  const fit = fitOf(view, space, narrow);
  const shows = (site: "agent" | "manage") => view === site || (view === "both" && !narrow);
  const built = slug !== null;
  const manageHeight = fit.stacked || fit.across
    ? STACKED_MANAGE_HEIGHT
    : Math.max(MIN_MANAGE_HEIGHT, (space.height - NAME_ROW) / fit.scale);
  // The frame's page follows the moment; an earlier one reached no page while it loaded.
  const frame = useRef<HTMLIFrameElement>(null);
  const tellMoment = () => {
    frame.current?.contentWindow?.postMessage({ type: "sesame:moment", moment }, window.location.origin);
  };
  const moments = useRef(moment);
  useEffect(() => {
    if (moments.current === moment) return;
    moments.current = moment;
    frame.current?.contentWindow?.postMessage({ type: "sesame:moment", moment }, window.location.origin);
  }, [moment]);

  // Each tab starts at its top; a fact then scrolls to its words.
  const lastView = useRef(view);
  useEffect(() => {
    if (lastView.current === view) return;
    lastView.current = view;
    if (view !== "public" || !fact?.box) paste.current?.scrollTo?.({ top: 0 });
  }, [view, fact]);
  const reveal = (top: number) => {
    const element = paste.current;
    if (!element) return;
    const visible = element.clientHeight * 0.4;
    element.scrollTo?.({ top: Math.max(0, top * fit.scale - visible), behavior: "smooth" });
  };

  return (
    <div ref={ref} className="st-canvas">
      <div className="st-cv-top">
        <div className="st-seg" role="group" aria-label="Which site to show">
          <button type="button" disabled={!built} aria-pressed={view === "agent"} onClick={() => onView("agent")}>
            <span className="st-tile">Ag</span>Agent site
          </button>
          <button type="button" disabled={!built} aria-pressed={view === "manage"} onClick={() => onView("manage")}>
            <span className="st-tile">Mg</span>Management
          </button>
          <button
            type="button"
            className="plain st-both"
            disabled={!built}
            aria-pressed={view === "both"}
            onClick={() => onView("both")}
          >
            Both
          </button>
          <button
            type="button"
            disabled={pages.length === 0}
            aria-pressed={view === "public"}
            onClick={() => onView("public")}
          >
            <span className="st-tile">Ws</span>Your public site
          </button>
        </div>
        <span className="st-zoom">{Math.round(fit.scale * 100)}%</span>
      </div>
      <div ref={paste} className={fit.stacked ? "st-paste stack" : "st-paste"} tabIndex={0} aria-label="Site preview">
        {slug && (
          <Frame name={NAMES.agent} fit={fit} hidden={!shows("agent")}>
            <AgentSheet
              client={client}
              slug={slug}
              live={live}
              today={today}
              addresses={addresses}
              tools={tools}
              fallback={fallback}
            />
          </Frame>
        )}
        {/* Stays mounted: a form being edited in the frame keeps its draft. */}
        {slug && (
          <Frame name={NAMES.manage} fit={fit} hidden={!shows("manage")} height={manageHeight}>
            {ready && (
              <iframe
                ref={frame}
                title="Management site"
                src={`/manage/${encodeURIComponent(slug)}`}
                onLoad={tellMoment}
              />
            )}
          </Frame>
        )}
        {view === "public" && (
          <Frame name={NAMES.public} fit={fit}>
            <PublicSheet pages={pages} fact={fact} width={fit.width} onReveal={reveal} />
          </Frame>
        )}
      </div>
      {built && (view === "manage" || view === "both") && (
        <Dock moment={moment} services={services} look={look} onMoment={onMoment} onColour={onColour} />
      )}
    </div>
  );
});

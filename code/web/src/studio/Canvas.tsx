import { forwardRef, useEffect, useRef, useState } from "react";
import type { Addresses, View } from "./feed.tsx";

const FRAME_WIDTH = 1000;
const NARROW = 900;
const NAME_ROW = 28;

const NAMES: Record<View, string> = {
  agent: "Agent site, no interface",
  manage: "Management site",
};

type CanvasProps = {
  slug: string;
  live: boolean;
  // Waits for the owner cookie, or the page behind it answers 401.
  ready: boolean;
  view: View;
  addresses: Addresses | null;
  onView: (view: View) => void;
};

// The gray pasteboard next to the chat. #151 adds the public site, "Both", the zoom readout and
// the agent site's frames.
export const Canvas = forwardRef<HTMLDivElement, CanvasProps>(function Canvas(
  { slug, live, ready, view, addresses, onView },
  ref,
) {
  return (
    <div ref={ref} className="st-canvas">
      <div className="st-cv-top">
        <div className="st-seg" role="group" aria-label="Which site to show">
          <button type="button" aria-pressed={view === "agent"} onClick={() => onView("agent")}>
            <span className="st-tile">Ag</span>Agent site
          </button>
          <button type="button" aria-pressed={view === "manage"} onClick={() => onView("manage")}>
            <span className="st-tile">Mg</span>Management
          </button>
        </div>
      </div>
      {/* Stays mounted: a form being edited in the frame keeps its draft. */}
      <ManageFrame slug={slug} ready={ready} hidden={view !== "manage"} />
      {view === "agent" && <AgentSheet slug={slug} live={live} addresses={addresses} />}
    </div>
  );
});

function ManageFrame({ slug, ready, hidden }: { slug: string; ready: boolean; hidden: boolean }) {
  const paste = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useEffect(() => {
    const element = paste.current;
    if (!element) return;
    const measure = () => {
      if (element.clientWidth === 0) return;
      const style = getComputedStyle(element);
      setSize({
        width: element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
        height: element.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const narrow = window.innerWidth <= NARROW;
  const width = narrow && size ? size.width : FRAME_WIDTH;
  const scale = size ? Math.min(1, size.width / width) : 1;
  const height = size ? Math.max(0, size.height - NAME_ROW) : 0;

  return (
    <div ref={paste} className="st-paste" hidden={hidden}>
      {size && (
        <div className="st-frame" style={{ width: width * scale }}>
          <div className="st-frame-name">{NAMES.manage}</div>
          <div className="st-sheet" style={{ width: width * scale, height }}>
            {ready && (
              <iframe
                title="Management site"
                src={`/manage/${encodeURIComponent(slug)}`}
                style={{ width, height: height / scale, transform: `scale(${scale})` }}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function AgentSheet({ slug, live, addresses }: { slug: string; live: boolean; addresses: Addresses | null }) {
  return (
    <div className="st-paste">
      <div className="st-frame st-frame-agent">
        <div className="st-frame-name">{NAMES.agent}</div>
        <div className="st-sheet st-wire">
          <div className="st-wire-top">
            <code>{addresses?.agent ?? `batp://…/${slug}`}</code>
            <span className={live ? "st-wire-live on" : "st-wire-live"}>{live ? "Live" : "Not live"}</span>
          </div>
          <p>
            No pages, no buttons, no design. Agents open a session on port 4710 and exchange one JSON
            frame per line. This is the whole site.
          </p>
          {!live && (
            <p className="st-wire-note">
              Right now every agent gets a 404. The first answer below is what they get once you
              publish.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

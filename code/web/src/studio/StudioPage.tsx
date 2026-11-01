import { useEffect, useRef } from "react";
import { useChat } from "../chat/useChat.ts";
import { managementClient } from "../management/index.ts";
import { Canvas } from "./Canvas.tsx";
import { CHAT_CLIENT, DEMO_MERCHANT, WELCOME } from "./chatClient.ts";
import { Composer, type ComposerHandle } from "./Composer.tsx";
import { EXAMPLES } from "./examples.ts";
import { Feed } from "./feed.tsx";
import { useSplitOpening } from "./motion.ts";
import { PaneSwitch } from "./PaneSwitch.tsx";
import { chipsOf, feedOf, isBuilt, isEmpty, phaseOf, pillOf, profileOf, siteName } from "./phase.ts";
import { Rail } from "./Rail.tsx";
import { TopBar } from "./TopBar.tsx";
import { useCanvasView } from "./useCanvasView.ts";
import { useCopy } from "./useCopy.ts";
import { addressesOf, useOverview } from "./useOverview.ts";
import { useTurns } from "./useTurns.ts";
import "./studio.css";

const EXAMPLE_ADDRESSES = EXAMPLES.map((example) => example.address);
const TITLE = "Sésame Studio";

/** Studio's state: the conversation, what the platform knows of its merchant, the canvas, the turns. */
function useStudio() {
  const chat = useChat(CHAT_CLIENT, WELCOME, DEMO_MERCHANT);
  const { state, claimed } = chat;
  const phase = phaseOf(state);
  const slug = state.merchant?.slug ?? null;
  const profile = profileOf(state.items);
  // A merchant found again after a reload, with no build in this conversation.
  const restored = slug !== null && profile === null && state.turn === 0;
  const overview = useOverview({ slug, claimed, restored, status: state.status, setStatus: chat.setStatus });
  const firstPage = state.sitePages.length > 0;
  const pages = firstPage ? state.sitePages : overview.pages;
  const canvas = useCanvasView({ items: state.items, profile, pageCount: pages.length, firstPage });
  const composer = useRef<ComposerHandle>(null);
  const turns = useTurns({ chat, phase, canvas, forget: overview.forget, composer });
  const addresses = addressesOf(slug, overview.merchant, profile?.mcp_url);
  return { chat, phase, slug, profile, restored, overview, pages, canvas, composer, turns, addresses };
}

export function StudioPage() {
  const { chat, phase, slug, profile, restored, overview, pages, canvas, composer, turns, addresses } = useStudio();
  const { state, claimed } = chat;
  const status = state.status;
  const empty = isEmpty(state);
  const { toast, copy } = useCopy();
  const body = useRef<HTMLElement>(null);
  const canvasPane = useRef<HTMLDivElement>(null);
  // As soon as there is a site to show: the page the crawl read, then the built sites.
  const split = slug !== null || pages.length > 0;
  useSplitOpening(split, body, canvasPane);

  useEffect(() => {
    document.title = TITLE;
  }, []);

  const pillReady = !restored || overview.merchant !== null;

  return (
    // A plain box, so that its header is the page's banner and its main a top-level landmark.
    <div className="studio" data-split={split ? "1" : "0"} data-pane={canvas.pane}>
      <TopBar
        name={siteName(state)}
        pill={pillReady ? pillOf(state) : null}
        built={isBuilt(state)}
        canPublish={phase === "review" && !state.busy && claimed}
        published={status === "live" || status === "down"}
        onNew={turns.startOver}
        onPublish={turns.publish}
      />

      <main ref={body} className="st-body">
        {/* The first question is the page's heading; after it, this one. */}
        {!empty && <h1 className="st-sr">{TITLE}</h1>}
        <Rail merchant={state.merchant?.name ?? null} onNew={turns.startOver} onExample={turns.openExample} />

        {split && <PaneSwitch pane={canvas.pane} onPane={canvas.setPane} />}

        <div className="st-chat" data-empty={empty ? "1" : undefined}>
          <Feed
            empty={empty}
            entries={feedOf(state)}
            activity={state.activity}
            busy={state.busy}
            addresses={addresses}
            onOpen={canvas.open}
            onFact={canvas.showFact}
            onCopy={copy}
            onRetry={turns.retry}
          />
          <Composer
            ref={composer}
            chips={chipsOf(state, phase, EXAMPLE_ADDRESSES)}
            empty={empty}
            phase={phase}
            busy={state.busy}
            onSay={turns.say}
            onChip={turns.chip}
          />
        </div>

        {split && (
          <Canvas
            ref={canvasPane}
            client={managementClient}
            slug={slug}
            today={overview.merchant?.now.slice(0, 10) ?? null}
            pages={pages}
            fact={canvas.fact}
            tools={profile?.tools ?? []}
            fallback={profile?.profile.fallback_url ?? null}
            live={phase === "live"}
            ready={claimed}
            view={canvas.view}
            addresses={addresses}
            onView={canvas.showView}
            moment={canvas.moment}
            services={profile?.profile.services ?? overview.services}
            look={overview.merchant?.look ?? profile?.profile.look ?? null}
            onMoment={canvas.showMoment}
            onColour={turns.askForColour}
          />
        )}
      </main>

      {toast && (
        <div className="st-toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { useChat } from "../chat/useChat.ts";
import { managementClient } from "../management/index.ts";
import { Canvas } from "./Canvas.tsx";
import { CHAT_CLIENT, DEMO_MERCHANT, WELCOME } from "./chatClient.ts";
import { Composer, type ComposerHandle } from "./Composer.tsx";
import { EXAMPLES } from "./examples.ts";
import { Feed } from "./feed.tsx";
import { Hello } from "./Hello.tsx";
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

export function StudioPage() {
  const chat = useChat(CHAT_CLIENT, WELCOME, DEMO_MERCHANT);
  const { state, claimed } = chat;
  const phase = phaseOf(state);
  const empty = isEmpty(state);
  const slug = state.merchant?.slug ?? null;
  const profile = profileOf(state.items);
  // A merchant found again after a reload, with no build in this conversation.
  const restored = slug !== null && profile === null && state.turn === 0;
  const status = state.status;
  const stored = useOverview({ slug, claimed, restored, status, setStatus: chat.setStatus });
  const { overview } = stored;
  const firstPage = state.sitePages.length > 0;
  const pages = firstPage ? state.sitePages : stored.pages;
  const canvas = useCanvasView({ items: state.items, profile, pageCount: pages.length, firstPage });
  const { toast, copy } = useCopy();
  const [input, setInput] = useState("");
  const body = useRef<HTMLDivElement>(null);
  const canvasPane = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  const turns = useTurns({ chat, phase, canvas, setInput, forget: stored.forget, composer });
  // As soon as there is a site to show: the page the crawl read, then the built sites.
  const split = slug !== null || pages.length > 0;
  useSplitOpening(split, body, canvasPane);

  useEffect(() => {
    document.title = "Sésame Studio";
  }, []);

  const addresses = addressesOf(slug, overview, profile?.mcp_url);
  const pill = pillOf(state);
  const pillReady = !restored || overview !== null;

  return (
    <section className="studio" data-split={split ? "1" : "0"} data-pane={canvas.pane} aria-label="Sésame">
      <TopBar
        name={siteName(state)}
        pill={pillReady ? pill : null}
        built={isBuilt(state)}
        canPublish={phase === "review" && !state.busy && claimed}
        published={status === "live" || status === "down"}
        onNew={turns.startOver}
        onPublish={turns.publish}
      />

      <div ref={body} className="st-body">
        <Rail merchant={state.merchant?.name ?? null} onNew={turns.startOver} onExample={turns.openExample} />

        {split && <PaneSwitch pane={canvas.pane} onPane={canvas.setPane} />}

        <div className="st-chat" data-empty={empty ? "1" : undefined}>
          {empty ? (
            <Hello />
          ) : (
            <Feed
              entries={feedOf(state)}
              activity={state.activity}
              busy={state.busy}
              addresses={addresses}
              onOpen={canvas.open}
              onFact={canvas.showFact}
              onCopy={copy}
              onRetry={turns.retry}
            />
          )}
          <Composer
            ref={composer}
            input={input}
            setInput={setInput}
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
            today={overview?.now.slice(0, 10) ?? null}
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
            services={profile?.profile.services ?? stored.services}
            look={overview?.look ?? profile?.profile.look ?? null}
            onMoment={canvas.showMoment}
            onColour={turns.askForColour}
          />
        )}
      </div>

      {toast && (
        <div className="st-toast" role="status">
          {toast}
        </div>
      )}
    </section>
  );
}

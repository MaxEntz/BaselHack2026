import { type ComponentType, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router";
import type { LiveEvent } from "../types.ts";
import { announcingChanges, onChange } from "./changes.ts";
import { ClientContext, useClient, useLoad } from "./data.ts";
import { RefreshContext, type RefreshStore, createRefreshStore, useLatestVersion, usePageVersion } from "./live.ts";
import { AgentPage } from "./pages/AgentPage.tsx";
import { FloorPage } from "./pages/FloorPage.tsx";
import { GuestsPage } from "./pages/GuestsPage.tsx";
import { HoursPage } from "./pages/HoursPage.tsx";
import { HomePage } from "./pages/home/HomePage.tsx";
import { guests, throughAgent } from "./pages/home/format.ts";
import { PageTitle } from "./pages/PageTitle.tsx";
import { ReservationsPage } from "./pages/reservations/ReservationsPage.tsx";
import { RulesPage } from "./pages/RulesPage.tsx";
import { SettingsPage } from "./pages/SettingsPage.tsx";
import { type PageId, PAGES, bookingPath, isPage, nameFromSlug, pagePath, pageTitle } from "./pages.ts";
import { AssistantPanel } from "./shell/AssistantPanel.tsx";
import { Footer } from "./shell/Footer.tsx";
import { Sidebar } from "./shell/Sidebar.tsx";
import { TopBar } from "./shell/TopBar.tsx";
import { useAssistantOpen } from "./shell/useAssistantOpen.ts";
import "./shell/shell.css";
import { ToastProvider, useToast } from "./ui/index.ts";

// The pages built so far; the others come with their issues (#140).
const VIEWS: Partial<Record<PageId, ComponentType<{ slug: string; version: number }>>> = {
  agent: AgentPage,
  guests: GuestsPage,
  floor: FloorPage,
  hours: HoursPage,
  reservations: ReservationsPage,
  rules: RulesPage,
  settings: SettingsPage,
};

/** One management page, in the shell (#137). */
export function ManagePage() {
  const { slug = "", page, "*": rest } = useParams();
  // Unknown pages go to Home.
  if (rest || (page !== undefined && !isPage(page))) return <Navigate to={pagePath(slug, "home")} replace />;
  return <ManageShell key={slug} slug={slug} page={page ?? "home"} />;
}

function ManageShell({ slug, page }: { slug: string; page: PageId }) {
  const base = useClient();
  // Every mutation of the site announces itself, so the pages read again (#81: /events doesn't).
  const client = useMemo(() => announcingChanges(base), [base]);
  const [store] = useState(createRefreshStore);
  const [assistantOpen, setAssistantOpen] = useAssistantOpen();
  const full = page === "assistant";
  return (
    <ClientContext.Provider value={client}>
      <RefreshContext.Provider value={store}>
        <div className={["mg", "mg-shell", assistantOpen && !full && "asst-on"].filter(Boolean).join(" ")} data-page={page}>
          <ToastProvider>
            <Shell
              slug={slug}
              page={page}
              store={store}
              assistantShown={full || assistantOpen}
              setAssistantOpen={setAssistantOpen}
            />
          </ToastProvider>
        </div>
      </RefreshContext.Provider>
    </ClientContext.Provider>
  );
}

function Shell({
  slug,
  page,
  store,
  assistantShown,
  setAssistantOpen,
}: {
  slug: string;
  page: PageId;
  store: RefreshStore;
  assistantShown: boolean;
  setAssistantOpen: (open: boolean) => void;
}) {
  const client = useClient();
  const navigate = useNavigate();
  const toast = useToast();
  const version = usePageVersion();
  const latest = useLatestVersion();
  const [unseen, setUnseen] = useState(0);
  const full = page === "assistant";

  const loadMerchant = useCallback((signal: AbortSignal) => client.merchant(slug, signal), [client, slug]);
  const merchant = useLoad(loadMerchant, latest, false).data ?? null;
  const name = merchant?.name ?? nameFromSlug(slug);

  // The live handler reads the latest render's state; the subscription itself stays open.
  const onLive = useRef<(event: LiveEvent) => void>(() => {});
  useEffect(() => {
    onLive.current = (event) => {
      store.bump();
      if (event.kind !== "new_booking") return;
      setUnseen((n) => n + 1);
      const { reservation: r } = event;
      if (assistantShown || !throughAgent(event.via)) return;
      toast(
        <>
          <b>New booking through your agent</b>
          {r.guest_name}, {r.time}, {guests(r.party_size)}, {r.table_label}
        </>,
        { label: "See it", run: () => navigate(bookingPath(slug, event.date, r.id)) },
      );
    };
  });
  useEffect(
    () =>
      client.subscribe(slug, {
        onEvent: (event) => onLive.current(event),
        onConnection: () => {},
      }),
    [client, slug],
  );
  useEffect(() => onChange(slug, store.bump), [slug, store]);

  useEffect(() => {
    document.title = pageTitle(name, page);
  }, [name, page]);
  useEffect(() => {
    document.scrollingElement?.scrollTo?.(0, 0);
  }, [page]);

  const home = () => navigate(pagePath(slug, "home"), { viewTransition: true });
  const View = VIEWS[page];
  return (
    <div className="app">
      <Sidebar slug={slug} page={page} name={name} merchant={merchant} />
      {!full && (
        <div className="mid">
          <TopBar slug={slug} page={page} merchant={merchant} unseen={unseen} onSeen={() => setUnseen(0)} />
          <main>
            {page === "home" ? (
              <HomePage slug={slug} version={version} onAsk={() => setAssistantOpen(true)} />
            ) : View ? (
              <View slug={slug} version={version} />
            ) : (
              <PageTitle title={PAGES[page]} />
            )}
            <Footer merchant={merchant} />
          </main>
        </div>
      )}
      <AssistantPanel
        shown={assistantShown}
        full={full}
        onResize={() => (full ? home() : navigate(pagePath(slug, "assistant"), { viewTransition: true }))}
        onClose={() => {
          setAssistantOpen(false);
          if (full) home();
        }}
      />
    </div>
  );
}

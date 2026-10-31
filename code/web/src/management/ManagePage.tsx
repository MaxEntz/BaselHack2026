import { type ComponentType, useEffect, useState } from "react";
import { Navigate, useParams } from "react-router";
import { useClient } from "./data.ts";
import { AgentPage } from "./pages/AgentPage.tsx";
import { FloorPage } from "./pages/FloorPage.tsx";
import { GuestsPage } from "./pages/GuestsPage.tsx";
import { HoursPage } from "./pages/HoursPage.tsx";
import { HomePage } from "./pages/home/HomePage.tsx";
import { RulesPage } from "./pages/RulesPage.tsx";
import { SettingsPage } from "./pages/SettingsPage.tsx";
import { type PageId, PAGES, isPage, nameFromSlug, pageTitle } from "./pages.ts";
import { ToastProvider } from "./ui/index.ts";

// The pages built so far; the others come with their issues (#137-#147).
const VIEWS: Partial<Record<PageId, ComponentType<{ slug: string; version: number }>>> = {
  home: HomePage,
  agent: AgentPage,
  guests: GuestsPage,
  floor: FloorPage,
  hours: HoursPage,
  rules: RulesPage,
  settings: SettingsPage,
};

/** One management page. */
export function ManagePage() {
  const { slug = "", page, "*": rest } = useParams();
  // Unknown pages go to Home.
  if (rest || (page !== undefined && !isPage(page))) return <Navigate to={`/manage/${slug}`} replace />;
  return <ManageShell slug={slug} page={page ?? "home"} />;
}

/** Bumps on every live event, so the page reads its data again. The shell (#137) replaces it. */
function useLiveVersion(slug: string): number {
  const client = useClient();
  const [version, setVersion] = useState(0);
  useEffect(
    () =>
      client.subscribe(slug, {
        onEvent: () => setVersion((v) => v + 1),
        onConnection: () => {},
      }),
    [slug, client],
  );
  return version;
}

function ManageShell({ slug, page }: { slug: string; page: PageId }) {
  const name = nameFromSlug(slug);
  const version = useLiveVersion(slug);
  useEffect(() => {
    document.title = pageTitle(name, page);
  }, [name, page]);
  const View = VIEWS[page];
  return (
    <div className="mg mg-page" data-page={page}>
      <ToastProvider>
        <main>
          {View ? (
            <View key={slug} slug={slug} version={version} />
          ) : (
            <>
              <h1>{PAGES[page]}</h1>
              <p>{name}</p>
            </>
          )}
        </main>
      </ToastProvider>
    </div>
  );
}

import { type ComponentType, useEffect } from "react";
import { Navigate, useParams } from "react-router";
import { HoursPage } from "./pages/HoursPage.tsx";
import { RulesPage } from "./pages/RulesPage.tsx";
import { SettingsPage } from "./pages/SettingsPage.tsx";
import { type PageId, PAGES, isPage, nameFromSlug, pageTitle } from "./pages.ts";
import { ToastProvider } from "./ui/index.ts";

// The pages built so far; the others come with their issues (#137-#147).
const VIEWS: Partial<Record<PageId, ComponentType<{ slug: string }>>> = {
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

function ManageShell({ slug, page }: { slug: string; page: PageId }) {
  const name = nameFromSlug(slug);
  useEffect(() => {
    document.title = pageTitle(name, page);
  }, [name, page]);
  const View = VIEWS[page];
  return (
    <div className="mg mg-page" data-page={page}>
      <ToastProvider>
        <main>
          {View ? (
            <View key={slug} slug={slug} />
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

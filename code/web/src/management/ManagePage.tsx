import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router";
import type { ManagementClient } from "./client.ts";
import { managementClient } from "./index.ts";
import { HomePage } from "./pages/home/HomePage.tsx";
import { type PageId, PAGES, isPage, nameFromSlug, pageTitle } from "./pages.ts";
import { ToastProvider } from "./ui/index.ts";

/** One management page. The pages themselves come with their issues (#137-#147). */
export function ManagePage({ client = managementClient }: { client?: ManagementClient }) {
  const { slug = "", page, "*": rest } = useParams();
  // Unknown pages go to Home.
  if (rest || (page !== undefined && !isPage(page))) return <Navigate to={`/manage/${slug}`} replace />;
  return <ManageShell slug={slug} page={page ?? "home"} client={client} />;
}

/** Bumps on every live event, so the page reads its data again. The shell (#137) replaces it. */
function useLiveVersion(slug: string, client: ManagementClient): number {
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

function ManageShell({ slug, page, client }: { slug: string; page: PageId; client: ManagementClient }) {
  const name = nameFromSlug(slug);
  const version = useLiveVersion(slug, client);
  useEffect(() => {
    document.title = pageTitle(name, page);
  }, [name, page]);
  return (
    <div className="mg mg-page" data-page={page}>
      <ToastProvider>
        <main>
          {page === "home" ? (
            <HomePage slug={slug} client={client} version={version} />
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

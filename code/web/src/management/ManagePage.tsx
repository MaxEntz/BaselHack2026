import { useEffect } from "react";
import { Navigate, useParams } from "react-router";
import { type PageId, PAGES, isPage, nameFromSlug, pageTitle } from "./pages.ts";
import { ToastProvider } from "./ui/index.ts";

/** One management page. The pages themselves come with their issues (#137-#147). */
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
  return (
    <div className="mg mg-page" data-page={page}>
      <ToastProvider>
        <main>
          <h1>{PAGES[page]}</h1>
          <p>{name}</p>
        </main>
      </ToastProvider>
    </div>
  );
}

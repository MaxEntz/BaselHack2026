import { Suspense, lazy, useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { acceptInvite } from "./chat/client.ts";
import { StudioPage } from "./studio/StudioPage.tsx";

// The review galleries (`?gallery`), in development and the mock only: a production build
// leaves them, and the fixtures they show, out (#220).
const Galleries =
  import.meta.env.DEV || import.meta.env.VITE_MOCK === "1"
    ? lazy(async () => {
        const [{ Gallery }, { PrimitivesGallery }] = await Promise.all([
          import("./cards/Gallery.tsx"),
          import("./management/PrimitivesGallery.tsx"),
        ]);
        return {
          default: () => (
            <>
              <PrimitivesGallery />
              <div className="app">
                <Gallery />
              </div>
            </>
          ),
        };
      })
    : null;

/** `/`: Studio, or the review galleries with `?gallery`. */
export function Studio() {
  const { search, pathname } = useLocation();
  const navigate = useNavigate();
  const invite = new URLSearchParams(search).get("invite");
  useEffect(() => {
    if (!invite || import.meta.env.VITE_MOCK === "1") return;
    const abort = new AbortController();
    // The code leaves the address bar once sent, so it isn't shared by a copied URL.
    void acceptInvite(invite, abort.signal).then((accepted) => {
      // A refused code stays in the address bar, so that it can be checked.
      if (accepted && !abort.signal.aborted) navigate(pathname, { replace: true });
    });
    return () => abort.abort();
  }, [invite, navigate, pathname]);
  if (Galleries && new URLSearchParams(search).has("gallery")) {
    return (
      <Suspense fallback={null}>
        <Galleries />
      </Suspense>
    );
  }
  return <StudioPage />;
}

import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import App from "./App.tsx";
import { acceptInvite } from "./chat/client.ts";
import { Gallery } from "./cards/Gallery.tsx";
import { PrimitivesGallery } from "./management/PrimitivesGallery.tsx";

/** `/`: Studio (the chat until #141), or the review galleries with `?gallery`. */
export function Studio() {
  const { search, pathname } = useLocation();
  const navigate = useNavigate();
  const invite = new URLSearchParams(search).get("invite");
  useEffect(() => {
    if (!invite || import.meta.env.VITE_MOCK === "1") return;
    // The code leaves the address bar once sent, so it isn't shared by a copied URL.
    void acceptInvite(invite).then((accepted) => {
      // A refused code stays in the address bar, so that it can be checked.
      if (accepted) navigate(pathname, { replace: true });
    });
  }, [invite, navigate, pathname]);
  if (new URLSearchParams(search).has("gallery")) {
    return (
      <>
        <PrimitivesGallery />
        <div className="app">
          <Gallery />
        </div>
      </>
    );
  }
  return <App />;
}

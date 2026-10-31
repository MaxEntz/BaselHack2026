import { useLocation } from "react-router";
import App from "./App.tsx";
import { Gallery } from "./cards/Gallery.tsx";
import { PrimitivesGallery } from "./management/PrimitivesGallery.tsx";

/** `/`: Studio (the chat until #141), or the review galleries with `?gallery`. */
export function Studio() {
  const { search } = useLocation();
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

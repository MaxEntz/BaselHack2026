import { useEffect } from "react";
import { useRouteError } from "react-router";
import "./studio/studio.css";

export const ROUTE_ERROR = "This page stopped working.";

/** What a page that fails to draw shows instead of React Router's developer screen (#220). */
export function RouteError() {
  const error = useRouteError();
  useEffect(() => {
    // The cause stays readable in the console, where the developer screen showed it.
    console.error(error);
  }, [error]);
  return (
    <section className="studio" aria-label="Sésame">
      <header className="st-top">
        <img className="st-logo" src="/wordmark.png" alt="Sésame" />
      </header>
      <div className="st-error">
        <div className="st-hello" role="alert">
          <h1>{ROUTE_ERROR}</h1>
          <p>What you saved is safe on the platform. Reload the page, or go back to Studio.</p>
        </div>
        <div className="st-error-actions">
          <button type="button" className="st-btn pri" onClick={() => window.location.reload()}>
            Reload the page
          </button>
          <a className="st-btn out" href="/">
            Back to Studio
          </a>
        </div>
      </div>
    </section>
  );
}

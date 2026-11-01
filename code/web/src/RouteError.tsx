import "./studio/studio.css";

export const ROUTE_ERROR = "This page stopped working.";

/**
 * What a page that fails to draw shows instead of React Router's developer screen (#220). React
 * and React Router log the error themselves.
 */
export function RouteError() {
  return (
    <section className="studio" aria-label="Sésame">
      <header className="st-top">
        <img className="st-logo" src="/wordmark.png" alt="Sésame" />
      </header>
      <div className="st-crash">
        <div className="st-hello" role="alert">
          <h1>{ROUTE_ERROR}</h1>
          <p>What you saved is safe on the platform. Reload the page, or go back to Studio.</p>
        </div>
        <div className="st-crash-actions">
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

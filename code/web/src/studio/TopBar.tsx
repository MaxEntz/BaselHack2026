import type { Pill } from "./phase.ts";
import { PUBLISH } from "./publish.ts";

type TopBarProps = {
  name: string | null | undefined;
  // Null until there is one to show.
  pill: Pill | null;
  // A build is done: Publish shows.
  built: boolean;
  canPublish: boolean;
  published: boolean;
  onNew: () => void;
  onPublish: () => void;
};

/** Studio's top bar: the site's name and status, New and Publish. */
export function TopBar({ name, pill, built, canPublish, published, onNew, onPublish }: TopBarProps) {
  return (
    <header className="st-top">
      <img className="st-logo" src="/wordmark.png" alt="Sésame" />
      {name && <span className="st-sep" />}
      {name && <span className="st-name">{name}</span>}
      {pill && (
        <span className={pill.live ? "st-status live" : "st-status"}>
          <i />
          <span>{pill.label}</span>
        </span>
      )}
      <div className="st-actions">
        <button type="button" className="st-btn out sm st-new" onClick={onNew}>
          New
        </button>
        {built && (
          <button type="button" className="st-btn pri sm" disabled={!canPublish} onClick={onPublish}>
            {published ? "Published" : PUBLISH}
          </button>
        )}
      </div>
    </header>
  );
}

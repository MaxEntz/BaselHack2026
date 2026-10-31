/**
 * Sticky bar under a form: hidden until an input changes. `error` (a validation message)
 * replaces "Unsaved changes" and is announced (role=alert). Put it inside the <form>:
 * Save submits it.
 */
export function SaveBar({ dirty, error, onDiscard }: { dirty: boolean; error?: string | null; onDiscard: () => void }) {
  if (!dirty) return null;
  return (
    <div className="savebar">
      {error ? <span role="alert">{error}</span> : <span>Unsaved changes</span>}
      <button type="button" className="pill sm" onClick={onDiscard}>
        Discard
      </button>
      <button type="submit" className="pill sm pri">
        Save
      </button>
    </div>
  );
}

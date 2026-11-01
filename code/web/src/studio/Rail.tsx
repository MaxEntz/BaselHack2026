import { DEMO, EXAMPLES } from "./examples.ts";
import { initials } from "./phase.ts";

type RailProps = {
  // The merchant of this conversation, if any.
  merchant: string | null;
  onNew: () => void;
  onExample: (address: string, brief: string) => void;
};

/**
 * The restaurants on the left: a new one, this conversation's, and the demo's examples. The
 * tiles' letters are hidden from screen readers, which read the name next to them.
 */
export function Rail({ merchant, onNew, onExample }: RailProps) {
  return (
    <nav className="st-rail" aria-label="Restaurants">
      <button type="button" onClick={onNew}>
        <span className="st-tile plus" aria-hidden="true">
          +
        </span>
        New
      </button>
      {merchant !== null && (
        <button type="button" aria-current="true">
          <span className="st-tile" aria-hidden="true">
            {initials(merchant)}
          </span>
          {merchant}
        </button>
      )}
      {DEMO && <span className="st-rail-label">Examples</span>}
      {EXAMPLES.map((example) => (
        <button key={example.slug} type="button" onClick={() => onExample(example.address, example.brief)}>
          <span className="st-tile" style={{ background: example.colour, color: example.ink }} aria-hidden="true">
            {initials(example.name)}
          </span>
          {example.name}
        </button>
      ))}
    </nav>
  );
}

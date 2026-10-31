import { type ReactNode, useEffect, useRef } from "react";
import { useLocation } from "react-router";
import { Icon } from "./primitives.tsx";

/**
 * Native modal <dialog>. Closes by its x, a click on the backdrop, Esc, and any route change.
 * `onClose` runs once per close, whichever way it happened.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const location = useLocation();
  const shownAt = useRef(location.key);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      shownAt.current = location.key;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open, location.key]);

  useEffect(() => {
    if (open && location.key !== shownAt.current) onClose();
  }, [location.key, open, onClose]);

  return (
    <dialog
      ref={ref}
      aria-labelledby="dlg-title"
      onClose={() => {
        if (open) onClose();
      }}
      onClick={(event) => {
        if (event.target === ref.current) ref.current?.close();
      }}
    >
      {open && (
        <div className="dlg">
          <div className="dlg-hd">
            <h2 id="dlg-title">{title}</h2>
            <button type="button" className="dlg-x" aria-label="Close" onClick={() => ref.current?.close()}>
              <Icon name="x" />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}

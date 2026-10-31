import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { type ShowToast, TOAST_ACTION_MS, TOAST_MS, type ToastAction, ToastContext } from "./toast-context.ts";

type Item = { id: number; message: ReactNode; action?: ToastAction };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const next = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => setItems((all) => all.filter((item) => item.id !== id)), []);

  const show = useCallback<ShowToast>(
    (message, action) => {
      const id = next.current++;
      setItems((all) => [...all, { id, message, action }]);
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        dismiss(id);
      }, action ? TOAST_ACTION_MS : TOAST_MS);
      timers.current.add(timer);
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((item) => (
          <div className="toast" key={item.id}>
            <div>{item.message}</div>
            {item.action && (
              <button
                type="button"
                onClick={() => {
                  dismiss(item.id);
                  item.action?.run();
                }}
              >
                {item.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

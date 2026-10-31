import { type ReactNode, createContext, useContext } from "react";

/** How long a toast stays: 3.6 s, or 9 s when it has an action. */
export const TOAST_MS = 3600;
export const TOAST_ACTION_MS = 9000;

export type ToastAction = { label: string; run: () => void };
export type ShowToast = (message: ReactNode, action?: ToastAction) => void;

export const ToastContext = createContext<ShowToast>(() => {});

export function useToast(): ShowToast {
  return useContext(ToastContext);
}

// ToastContext (issue #95): shell singleton toast system (navy, bottom-center).
// No existing view triggers a toast yet — this issue builds only the mechanism
// (context/provider + hook, rendered once at Shell level).
// The provider exposes a `toast(message)` function; Shell renders the outlet.

import { createContext, useContext, useState, useCallback, useRef } from "react";

export type Toast = {
  id: number;
  message: string;
};

type ToastContextValue = {
  toast: (message: string) => void;
  toasts: Toast[];
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const TOAST_DURATION_MS = 4000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string) => {
      const id = ++nextId.current;
      setToasts((prev) => [...prev, { id, message }]);
      setTimeout(() => dismiss(id), TOAST_DURATION_MS);
    },
    [dismiss]
  );

  return (
    <ToastContext.Provider value={{ toast, toasts, dismiss }}>
      {children}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}

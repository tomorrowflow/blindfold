// ToastOutlet (issue #95): renders the toast stack (navy, bottom-center).
// Rendered exactly once, at Shell level. Consumers call useToast().toast(msg).

import { useToast } from "./ToastContext";
import { X } from "./icons";

export function ToastOutlet() {
  const { toasts, dismiss } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div className="bf-toast-outlet" aria-live="polite" aria-label="Notifications">
      {toasts.map((t) => (
        <div key={t.id} className="bf-toast" data-testid="toast" role="status">
          <span className="bf-toast-message">{t.message}</span>
          <button
            type="button"
            className="bf-toast-dismiss"
            aria-label="Dismiss notification"
            onClick={() => dismiss(t.id)}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

type ToastKind = "default" | "success";
type ShowToast = (text: string, kind?: ToastKind) => void;

const ToastCtx = createContext<ShowToast>(() => {});

export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Array<{ id: number; text: string; kind: ToastKind }>>([]);

  const show = useCallback<ShowToast>((text, kind = "default") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div className="toast-host" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="toast flex items-center gap-2.5 rounded-xl border bg-card px-4 py-3 text-sm font-medium shadow-lg">
            {t.kind === "success" && (
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                <Icon name="check" className="h-3 w-3" />
              </span>
            )}
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

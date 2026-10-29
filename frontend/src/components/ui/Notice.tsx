import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

const tones = {
  success: "bg-success text-success-foreground",
  warning: "bg-warning text-warning-foreground",
  error: "bg-destructive/10 text-destructive",
  info: "bg-info text-info-foreground",
};

/** An inline message in a form or step. Errors and warnings are announced. */
export function Notice({ tone, className, children }: { tone: keyof typeof tones; className?: string; children: ReactNode }) {
  return (
    <div role={tone === "success" || tone === "info" ? "status" : "alert"}
      className={cn("mt-6 rounded-xl px-4 py-3 text-sm font-medium", tones[tone], className)}>
      {children}
    </div>
  );
}

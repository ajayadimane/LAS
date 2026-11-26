import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

interface PageHeaderProps {
  title: ReactNode;
  subtitle?: ReactNode;
  /** Shows a square back button before the title. */
  onBack?: () => void;
  backLabel?: string;
  /** Right-hand slot: buttons, a "Step n of N" pill. */
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ title, subtitle, onBack, backLabel = "Back", actions, className }: PageHeaderProps) {
  return (
    <header className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
      <div className="flex min-w-0 items-start gap-4">
        {onBack && (
          <button onClick={onBack} aria-label={backLabel}
            className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border bg-card shadow-sm hover:bg-muted">
            <Icon name="left" />
          </button>
        )}
        <div className="min-w-0">
          <h1 className="text-2xl font-extrabold tracking-tight sm:text-3xl">{title}</h1>
          {subtitle && <div className="mt-1 text-sm text-muted-foreground">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-3">{actions}</div>}
    </header>
  );
}

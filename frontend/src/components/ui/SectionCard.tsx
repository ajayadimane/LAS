import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

interface SectionCardProps {
  title: ReactNode;
  subtitle?: ReactNode;
  /** A number badge ("1") or an icon tile before the title. */
  step?: number;
  icon?: IconName;
  /** Right side of the header: a pill, a button. */
  aside?: ReactNode;
  /** Grey strip at the bottom: "+ Add services", form buttons. */
  footer?: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
  children?: ReactNode;
}

export function SectionCard({ title, subtitle, step, icon, aside, footer, className, bodyClassName, id, children }: SectionCardProps) {
  return (
    <section id={id} className={cn("overflow-hidden rounded-2xl border bg-card shadow-card", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-5 sm:px-7">
        <div className="flex min-w-0 items-start gap-3">
          {step !== undefined && (
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-success text-sm font-bold text-accent-foreground">
              {step}
            </span>
          )}
          {icon && (
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary/15 bg-accent text-accent-foreground">
              <Icon name={icon} className="h-5 w-5" />
            </span>
          )}
          <div className="min-w-0">
            <h2 className="text-lg font-bold tracking-tight sm:text-xl">{title}</h2>
            {subtitle && <div className="mt-0.5 text-sm text-muted-foreground">{subtitle}</div>}
          </div>
        </div>
        {aside && <div className="flex flex-wrap items-center gap-2">{aside}</div>}
      </div>
      {children !== undefined && <div className={cn("px-5 py-6 sm:px-7", bodyClassName)}>{children}</div>}
      {footer && <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/50 px-5 py-4 sm:px-7">{footer}</div>}
    </section>
  );
}

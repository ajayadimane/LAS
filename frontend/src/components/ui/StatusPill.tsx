import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

const tones = {
  success: "border-success-foreground/20 bg-success text-success-foreground",
  warning: "border-warning-foreground/20 bg-warning text-warning-foreground",
  info: "border-info-foreground/20 bg-info text-info-foreground",
  neutral: "border-neutral-foreground/15 bg-neutral text-neutral-foreground",
  danger: "border-danger-foreground/20 bg-danger text-danger-foreground",
};

export type Tone = keyof typeof tones;

interface StatusPillProps {
  tone: Tone;
  /** A small coloured dot before the label (the Figma "Report ready" style). */
  dot?: boolean;
  icon?: IconName;
  className?: string;
  children: ReactNode;
}

export function StatusPill({ tone, dot, icon, className, children }: StatusPillProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold", tones[tone], className)}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />}
      {icon && <Icon name={icon} className="h-3.5 w-3.5" />}
      {children}
    </span>
  );
}

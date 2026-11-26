import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

// Tinted summary cards from the Figma redesign. Colours come from the theme tokens only.
const tones = {
  plain: { card: "bg-card", text: "", strong: "", icon: "bg-muted text-foreground", caption: "text-muted-foreground" },
  warning: {
    card: "border-warning-foreground/20 bg-gradient-to-br from-warning/60 to-card",
    text: "text-foreground", strong: "text-warning-foreground", icon: "border border-warning-foreground/25 bg-warning/70 text-warning-foreground",
    caption: "text-warning-foreground",
  },
  info: {
    card: "border-info-foreground/15 bg-gradient-to-br from-card to-info/70",
    text: "text-foreground", strong: "text-info-foreground", icon: "border border-info-foreground/20 bg-info text-info-foreground",
    caption: "text-muted-foreground",
  },
  success: {
    card: "border-success-foreground/20 bg-gradient-to-br from-success/80 to-success/40",
    text: "text-accent-foreground", strong: "text-accent-foreground", icon: "bg-success-foreground/15 text-accent-foreground",
    caption: "text-accent-foreground",
  },
  /** The highlighted card ("Total Paid"). */
  primary: {
    card: "border-primary/30 bg-gradient-to-br from-accent to-primary/25 shadow-card",
    text: "text-accent-foreground", strong: "text-accent-foreground", icon: "bg-primary text-primary-foreground",
    caption: "text-accent-foreground",
  },
  danger: {
    card: "border-danger-foreground/20 bg-gradient-to-br from-danger to-card",
    text: "text-danger-foreground", strong: "text-danger-foreground", icon: "bg-card text-danger-foreground",
    caption: "text-danger-foreground",
  },
};

export type KpiTone = keyof typeof tones;

interface KpiCardProps {
  label: ReactNode;
  value: ReactNode;
  tone?: KpiTone;
  icon?: IconName;
  /** Small line under the value; `dot` puts a coloured dot before it. */
  caption?: ReactNode;
  dot?: boolean;
  /** Bottom-right slot, e.g. "View Account >". */
  action?: ReactNode;
  /** Colours the label and value with the tone (Correction Summary cards). */
  toneText?: boolean;
  className?: string;
}

export function KpiCard({ label, value, tone = "plain", icon, caption, dot, action, toneText, className }: KpiCardProps) {
  const t = tones[tone];
  const accent = toneText ? t.strong : t.text;
  return (
    <div className={cn("flex flex-col rounded-2xl border p-5 shadow-sm", t.card, className)}>
      <div className="flex items-start justify-between gap-3">
        <p className={cn("max-w-[11rem] text-sm font-semibold leading-snug", accent)}>{label}</p>
        {icon && (
          <span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", t.icon)}>
            <Icon name={icon} className="h-5 w-5" />
          </span>
        )}
      </div>
      <div className={cn("mt-2 text-3xl font-extrabold tracking-tight", accent)}>{value}</div>
      {(caption || action) && (
        <div className="mt-auto flex items-center justify-between gap-3 pt-2 text-xs">
          {caption && (
            <span className={cn("flex items-center gap-1.5 font-medium", toneText ? t.strong : t.caption)}>
              {dot && <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden="true" />}
              {caption}
            </span>
          )}
          {action}
        </div>
      )}
    </div>
  );
}

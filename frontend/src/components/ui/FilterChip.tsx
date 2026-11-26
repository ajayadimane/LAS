import { cn } from "@/lib/cn";

const tones = {
  navy: { on: "border-navy bg-navy text-navy-foreground", off: "border-border bg-card text-foreground", badge: "bg-navy-foreground/15" },
  info: { on: "border-info-foreground/40 bg-info text-info-foreground ring-2 ring-info-foreground/20", off: "border-info-foreground/20 bg-info/60 text-info-foreground", badge: "bg-card" },
  warning: { on: "border-warning-foreground/40 bg-warning text-warning-foreground ring-2 ring-warning-foreground/20", off: "border-warning-foreground/20 bg-warning/60 text-warning-foreground", badge: "bg-card" },
  danger: { on: "border-danger-foreground/40 bg-danger text-danger-foreground ring-2 ring-danger-foreground/20", off: "border-danger-foreground/20 bg-danger/60 text-danger-foreground", badge: "bg-card" },
};

interface FilterChipProps {
  label: string;
  count?: number;
  tone?: keyof typeof tones;
  active: boolean;
  onClick: () => void;
}

/** Filter pill with a count badge (customer list). */
export function FilterChip({ label, count, tone = "navy", active, onClick }: FilterChipProps) {
  const t = tones[tone];
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={cn("inline-flex h-10 items-center gap-2 rounded-full border px-4 text-sm font-semibold transition-colors", active ? t.on : t.off)}>
      {label}
      {count !== undefined && (
        <span className={cn("min-w-6 rounded-full px-1.5 text-xs font-bold leading-6", tone === "navy" && !active ? "bg-muted" : t.badge)}>
          {count}
        </span>
      )}
    </button>
  );
}

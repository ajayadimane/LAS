import { cn } from "@/lib/cn";

/** "LAS" tile plus wordmark. `subtitle` replaces the wordmark with a two-line title block. */
export function Logo({ dark = false, title = "LAS", subtitle }: { dark?: boolean; title?: string; subtitle?: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-sm font-extrabold tracking-tight text-primary-foreground shadow-cta">
        LAS
      </span>
      <span className="min-w-0 leading-tight">
        <span className={cn("block font-extrabold tracking-tight", subtitle ? "text-base" : "text-lg", dark ? "text-navy-foreground" : "text-foreground")}>{title}</span>
        {subtitle && <span className={cn("block text-xs", dark ? "text-navy-muted" : "text-muted-foreground")}>{subtitle}</span>}
      </span>
    </div>
  );
}

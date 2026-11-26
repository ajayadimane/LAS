import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

interface StepperProps {
  steps: string[];
  /** 1-based. Steps before it are completed. */
  current: number;
  /** Lets the user jump back to an earlier or reachable step. */
  onSelect?: (step: number) => void;
  /** Highest step that may be opened (defaults to `current`). */
  reachable?: number;
  /**
   * How many steps are actually done (defaults to `current - 1`, i.e. a wizard).
   * An existing customer's page passes their real progress, so looking at an
   * earlier step does not mark the later ones "Upcoming".
   */
  completed?: number;
  className?: string;
}

/** Numbered progress with "Completed / In progress / Upcoming" captions. */
export function Stepper({ steps, current, onSelect, reachable = current, completed = current - 1, className }: StepperProps) {
  return (
    <ol className={cn("flex items-center gap-2 overflow-x-auto rounded-2xl border bg-card px-5 py-4 shadow-card", className)}>
      {steps.map((label, i) => {
        const n = i + 1;
        const done = n <= completed && n !== current;
        const active = n === current;
        const clickable = !!onSelect && n !== current && n <= reachable;
        const Tag = clickable ? "button" : "div";
        return (
          <li key={label} className="flex min-w-0 flex-1 items-center gap-2">
            <Tag
              {...(clickable ? { type: "button" as const, onClick: () => onSelect(n) } : {})}
              aria-current={active ? "step" : undefined}
              className={cn("flex min-w-0 items-center gap-3 text-left", clickable && "rounded-lg hover:opacity-80")}
            >
              <span className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold",
                done || active ? "bg-primary text-primary-foreground" : "border bg-muted text-muted-foreground",
                active && "ring-4 ring-primary/15",
              )}>
                {done ? <Icon name="check" className="h-4 w-4" /> : n}
              </span>
              <span className="min-w-0">
                <span className={cn("block text-[10px] font-bold uppercase tracking-wider",
                  done || active ? "text-primary" : "text-muted-foreground")}>
                  {done ? "Completed" : active ? "In progress" : "Upcoming"}
                </span>
                <span className={cn("block truncate text-sm", active ? "font-bold" : done ? "font-semibold" : "text-muted-foreground")}>
                  {label}
                </span>
              </span>
            </Tag>
            {n < steps.length && (
              <span className={cn("mx-1 hidden h-0.5 min-w-4 flex-1 rounded-full sm:block", done ? "bg-primary" : "bg-border")} aria-hidden="true" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** "• Step 2 of 4" pill for the page header. */
export function StepBadge({ current, total }: { current: number; total: number }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-accent px-3 py-1 text-xs font-semibold text-accent-foreground">
      <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
      Step {current} of {total}
    </span>
  );
}

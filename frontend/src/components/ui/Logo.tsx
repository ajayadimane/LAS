import { cn } from "@/lib/cn";

export function Logo({ dark = false }: { dark?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-xs font-extrabold tracking-tight text-primary-foreground">
        LAS
      </span>
      <span className={cn("text-lg font-extrabold tracking-tight", dark ? "text-navy-foreground" : "text-foreground")}>LAS</span>
    </div>
  );
}

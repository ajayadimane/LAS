import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

const toneText = { info: "text-info-foreground", warning: "text-warning-foreground" };

export function StatCard({ label, value, tone }: { label: string; value: ReactNode; tone?: keyof typeof toneText }) {
  const color = tone ? toneText[tone] : "";
  return (
    <div className="rounded-2xl border bg-card p-6">
      <p className={cn("text-sm font-semibold", color)}>{label}</p>
      <p className={cn("mt-3 text-3xl font-extrabold", color)}>{value}</p>
    </div>
  );
}

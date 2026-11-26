import type { ReactNode } from "react";
import { KpiCard } from "./KpiCard";

/** The plain KPI tile; a thin wrapper over KpiCard kept for existing callers. */
export function StatCard({ label, value, tone }: { label: string; value: ReactNode; tone?: "info" | "warning" }) {
  return <KpiCard label={label} value={value} tone={tone ?? "plain"} />;
}

import type { IconName } from "@/components/ui/Icon";

/** Icon per service code (app/core/config.py SERVICES); anything new gets a file icon. */
const ICONS: Record<string, IconName> = {
  DPR_GENERATION: "clipboard",
  DPR_ANALYSIS: "chartsearch",
  GEO_CHECK: "compass",
  VALUE_ADDITION: "shieldcheck",
  DOC_VERIFICATION: "listcheck",
};

export const serviceIcon = (code: string): IconName => ICONS[code] ?? "file";

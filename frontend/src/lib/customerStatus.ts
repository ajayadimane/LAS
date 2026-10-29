/**
 * How the vendor's per-customer status custom fields read on screen.
 *
 * Doc_Status, Payment_Status and Payment_Mode arrive as raw vendor strings;
 * "NA" (not set) arrives as null. The middleware writes "Paid" / "Processing",
 * "Pay now" / "Pay later" and "Uploaded" / "Partially uploaded" (config in
 * app/core/config.py); matching ignores case and spaces. An unrecognised value
 * is shown as-is (humanised) with a neutral pill and does not count towards
 * any filter, so nothing is silently misfiled.
 */

import type { Tone } from "@/components/ui/StatusPill";

export type DocBucket = "not-uploaded" | "awaiting" | "done" | "other";
export type PayBucket = "not-due" | "pending" | "paid" | "other";

interface Reading<B> {
  label: string;
  tone: Tone;
  bucket: B;
}

const DOC: Record<string, Reading<DocBucket>> = {
  PARTIALLY_UPLOADED: { label: "Partly uploaded", tone: "warning", bucket: "not-uploaded" },
  UPLOADED: { label: "Awaiting report", tone: "info", bucket: "awaiting" },
  AWAITING_REVIEW: { label: "Awaiting report", tone: "info", bucket: "awaiting" },
  APPROVED: { label: "Uploaded", tone: "success", bucket: "done" },
  AUTHORIZED: { label: "Uploaded", tone: "success", bucket: "done" },
};

const PAY: Record<string, Reading<PayBucket>> = {
  PENDING: { label: "Pending", tone: "warning", bucket: "pending" },
  PROCESSING: { label: "Processing", tone: "warning", bucket: "pending" },
  PAID: { label: "Paid", tone: "success", bucket: "paid" },
};

const MODE: Record<string, string> = { PAY_NOW: "Pay now", PAY_LATER: "Pay later" };

/** "PAY_LATER" / "pay later" -> "Pay later" */
const humanise = (raw: string) => {
  const words = raw.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const key = (raw: string) => raw.trim().toUpperCase().replace(/[\s-]+/g, "_");

export function readDocStatus(raw: string | null): Reading<DocBucket> {
  if (raw === null) return { label: "Not uploaded", tone: "neutral", bucket: "not-uploaded" };
  return DOC[key(raw)] ?? { label: humanise(raw), tone: "neutral", bucket: "other" };
}

export function readPaymentStatus(raw: string | null): Reading<PayBucket> {
  if (raw === null) return { label: "Not due", tone: "neutral", bucket: "not-due" };
  return PAY[key(raw)] ?? { label: humanise(raw), tone: "neutral", bucket: "other" };
}

export function readPaymentMode(raw: string | null): string | null {
  return raw === null ? null : (MODE[key(raw)] ?? humanise(raw));
}

/** A service's own lifecycle field (e.g. GeoCheck). */
const SERVICE: Record<string, { label: string; tone: Tone }> = {
  OPTED: { label: "Awaiting documents", tone: "warning" },
  DOCS_UPLOADED: { label: "Awaiting report", tone: "info" },
  APPROVED: { label: "Approved", tone: "success" },
  REJECTED: { label: "Rejected: upload again", tone: "warning" },
  REPORT_READY: { label: "Report ready", tone: "success" },
};

export function readServiceStatus(raw: string | null): { label: string; tone: Tone } {
  if (raw === null) return { label: "Not opted", tone: "neutral" };
  return SERVICE[key(raw)] ?? { label: humanise(raw), tone: "neutral" };
}

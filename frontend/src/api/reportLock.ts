import { ApiError } from "./client";

/** Why the report endpoints refuse (their 403 body: { detail: { detail, status, reasons } }). */
const reasonsOf = (error: unknown): string[] =>
  error instanceof ApiError && error.status === 403
    ? ((error.body as { detail?: { reasons?: string[] } } | undefined)?.detail?.reasons ?? [])
    : [];

/**
 * The analysis is finished and only the customer's Pay later payment is
 * missing. Nothing changes until the BD confirms it, so there is nothing to
 * poll for: each poll would be a 403 plus a vendor call for no reason.
 */
export const lockedOnlyForPayment = (error: unknown): boolean => {
  const reasons = reasonsOf(error);
  return reasons.includes("CUSTOMER_PAYMENT_DUE") && !reasons.includes("REPORT_NOT_COMPLETED");
};

/**
 * `refetchInterval` for the report endpoints: poll every 10 s while the
 * analysis is still running (a 403), but never once the report is ready and
 * locked only for payment (see lockedOnlyForPayment).
 */
export const pollWhileAnalysing = (query: { state: { data: unknown; error: unknown } }): number | false =>
  query.state.data || !(query.state.error instanceof ApiError) || query.state.error.status !== 403
    ? false
    : lockedOnlyForPayment(query.state.error)
      ? false
      : 10_000;

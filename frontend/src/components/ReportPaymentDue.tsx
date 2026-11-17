import type { ReactNode } from "react";
import { lockedOnlyForPayment } from "@/api/reportLock";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { inrShort } from "@/lib/format";

/** The reason the report endpoints give while a Pay later customer has not paid yet. */
export const CUSTOMER_PAYMENT_DUE = "CUSTOMER_PAYMENT_DUE";

/** The report is finished and locked only because the customer still owes the Pay later amount. */
export const isCustomerPaymentDue = (reasons: string[] | undefined) =>
  !!reasons?.includes(CUSTOMER_PAYMENT_DUE) && !reasons.includes("REPORT_NOT_COMPLETED");

export { lockedOnlyForPayment };

/**
 * A finished report that is locked because it was paid by Pay later and the
 * customer still owes the amount. Locked for the customer and the BD alike
 * (user, 2026-11-16); `children` is each side's own action: the customer's
 * payment QR, the BD's "customer has paid" button.
 */
export function ReportPaymentDue({
  amount,
  children,
  onCheck,
  checking,
}: {
  amount: number | null;
  children?: ReactNode;
  /** Re-ask the server once: nothing polls while the report waits for payment. */
  onCheck?: () => void;
  checking?: boolean;
}) {
  return (
    <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-500/40 dark:bg-amber-500/10">
      <div className="flex items-center gap-2">
        <Icon name="report" className="h-4 w-4" />
        <p className="text-sm font-bold">Your report is ready -- payment due</p>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        This service was paid by Pay later{amount !== null ? <>, so <b>{inrShort(amount)}</b> is still due</> : ""}.
        The report can be viewed or downloaded once the payment is received.
      </p>
      {children && <div className="mt-3">{children}</div>}
      {onCheck && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" disabled={checking} onClick={onCheck}>
            {checking ? "Checking…" : "Check payment status"}
          </Button>
          <span className="text-xs text-muted-foreground">This page does not refresh by itself while payment is pending.</span>
        </div>
      )}
    </div>
  );
}

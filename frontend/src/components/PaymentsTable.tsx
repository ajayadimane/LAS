import { useState, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useCatalog, type CustomerService, type CustomerServices } from "@/api/services";
import { Button } from "@/components/ui/Button";
import { Icon, type IconName } from "@/components/ui/Icon";
import { StatusPill, type Tone } from "@/components/ui/StatusPill";
import { useToast } from "@/components/ui/Toast";
import { readPaymentMode, readPaymentStatus } from "@/lib/customerStatus";
import { cn } from "@/lib/cn";
import { copyText, inr } from "@/lib/format";
import { serviceIcon } from "@/lib/serviceIcons";

/**
 * Per-service payment details, for the customer's Billing page and the BD's
 * customer preview: amount, mode, status and the pool transfer's transaction
 * id where one exists (Pay later). Pay now through the placeholder QR has no
 * transaction id. The joining fee was charged with the first payment; which
 * payment that was is not recorded, so it is listed on its own line.
 */

export type Viewer = "customer" | "bd";

const isPaid = (s: CustomerService) => !s.requested && readPaymentStatus(s.payment_status).bucket === "paid";
const isPayLater = (s: CustomerService) => readPaymentMode(s.payment_mode) === "Pay later";

function paymentState(s: CustomerService, viewer: Viewer): { label: string; tone: Tone } {
  if (s.requested) {
    const bd = viewer === "bd" ? "your" : "BD";
    return isPayLater(s)
      ? { label: `Awaiting ${bd} guarantee`, tone: "warning" }
      : { label: `Awaiting ${bd} confirmation`, tone: "warning" };
  }
  const { label, tone } = readPaymentStatus(s.payment_status);
  return { label, tone };
}

/** The services with a payment (opted or requested), the joining fee if charged, and totals. */
export function usePayments(data: CustomerServices | undefined) {
  const catalog = useCatalog();
  const rows = data?.services.filter((s) => s.opted || s.requested) ?? [];
  const joiningFee = data && !data.first_payment ? (catalog.data?.joining_fee ?? null) : null;
  return {
    rows,
    joiningFee,
    totalPaid: rows.filter(isPaid).reduce((sum, s) => sum + s.fee, 0) + (joiningFee ?? 0),
    payLater: rows.filter((s) => isPaid(s) && isPayLater(s)).length,
    awaiting: rows.filter((s) => !isPaid(s)).length,
  };
}

export function PaymentsTable({ query, viewer }: { query: UseQueryResult<CustomerServices>; viewer: Viewer }) {
  const { rows, joiningFee } = usePayments(query.data);

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="border-b bg-muted/60 text-left text-xs font-semibold text-muted-foreground">
            <tr>
              <th className="px-5 py-4">Service</th>
              <th className="px-5 py-4 text-right">Amount</th>
              <th className="px-5 py-4">Payment mode</th>
              <th className="px-5 py-4">Status</th>
              <th className="px-5 py-4">Transaction ID</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {query.isPending ? (
              <Message>Loading payments…</Message>
            ) : query.isError ? (
              <Message>
                <p>{query.error.message}</p>
                <Button variant="outline" size="sm" className="mt-3 rounded-lg" onClick={() => query.refetch()}>Try again</Button>
              </Message>
            ) : rows.length === 0 ? (
              <Message>No payments yet.</Message>
            ) : (
              <>
                {rows.map((s) => {
                  const state = paymentState(s, viewer);
                  return (
                    <tr key={s.code}>
                      <td className="px-5 py-4">
                        <span className="flex items-center gap-3 font-bold">
                          <RowIcon icon={serviceIcon(s.code)} solid /> {s.name}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-right font-semibold tabular-nums">{inr(s.fee)}</td>
                      <td className="px-5 py-4">{readPaymentMode(s.payment_mode) ?? "–"}</td>
                      <td className="px-5 py-4">
                        <StatusPill tone={state.tone} icon={state.tone === "success" ? "checkcircle" : undefined}>{state.label}</StatusPill>
                      </td>
                      <td className="px-5 py-4"><TransactionId id={s.transaction_id} /></td>
                    </tr>
                  );
                })}
                {joiningFee !== null && (
                  <tr className="bg-muted/30">
                    <td className="px-5 py-4">
                      <span className="flex items-center gap-3">
                        <RowIcon icon="checkcircle" />
                        <span>
                          <span className="block font-bold">Joining fee</span>
                          <span className="mt-0.5 block text-xs text-muted-foreground">One-time, charged with the first payment</span>
                        </span>
                      </span>
                    </td>
                    <td className="px-5 py-4 text-right font-semibold tabular-nums">{inr(joiningFee)}</td>
                    <td className="px-5 py-4">–</td>
                    <td className="px-5 py-4"><StatusPill tone="success" icon="checkcircle">Paid</StatusPill></td>
                    <td className="px-5 py-4"><TransactionId id={null} /></td>
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>
      {query.data?.payment_in_progress && (
        <p className="border-t bg-warning/40 px-5 py-3 text-sm text-warning-foreground">
          A payment is being confirmed. Its status will update once it is done.
        </p>
      )}
    </>
  );
}

function TransactionId({ id }: { id: string | null }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  if (!id) return <span className="text-muted-foreground">–</span>;

  const copy = async () => {
    if (await copyText(id)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      toast("Could not copy the transaction ID", "default");
    }
  };

  return (
    <span className="flex items-center gap-2">
      <span className="rounded-lg border bg-muted/50 px-2.5 py-1 font-mono text-xs">{id}</span>
      <button className="flex items-center gap-1 rounded-lg border px-2 py-1 text-xs font-medium text-primary hover:bg-accent" onClick={copy}>
        <Icon name={copied ? "check" : "copy"} className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

function RowIcon({ icon, solid }: { icon: IconName; solid?: boolean }) {
  return (
    <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border",
      solid ? "border-primary/20 bg-accent text-accent-foreground" : "bg-card text-muted-foreground")}>
      <Icon name={icon} className="h-4 w-4" />
    </span>
  );
}

function Message({ children }: { children: ReactNode }) {
  return (
    <tr>
      <td colSpan={5} className="px-5 py-10 text-center text-sm text-muted-foreground">{children}</td>
    </tr>
  );
}

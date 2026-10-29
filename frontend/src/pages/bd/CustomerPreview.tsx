import { useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useBdCustomer } from "@/api/bd";
import { ApiError } from "@/api/client";
import { useCustomerServices } from "@/api/services";
import { useSession } from "@/auth/AuthContext";
import { PaymentsTable, usePayments } from "@/components/PaymentsTable";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { StatusPill } from "@/components/ui/StatusPill";
import { cn } from "@/lib/cn";
import { address, fullName, phone } from "@/lib/customer";
import { readDocStatus, readPaymentMode, readPaymentStatus } from "@/lib/customerStatus";
import { initials, inr } from "@/lib/format";
import { paths } from "@/routes";
import { EditDetailsDialog } from "./EditDetails";
import { ServicesPayment } from "./ServicesPayment";

/**
 * One customer of the signed-in BD.
 *
 * The middleware returns 404 unless the person is registered under this BD.
 * Email and address are editable (EditDetails); name and mobile have no update API yet.
 */
export default function CustomerPreview() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [params] = useSearchParams();
  const session = useSession()!; // RequireRole guarantees a BD session
  const query = useBdCustomer(session.userId, id ?? "");
  const customer = query.data;
  const [editing, setEditing] = useState(false);
  const payments = useCustomerServices(session.userId, customer?.person_id ?? null);
  const { totalPaid } = usePayments(payments.data);
  const notFound = query.error instanceof ApiError && query.error.status === 404;

  const back = (
    <button onClick={() => navigate(paths.bdDashboard)}
      className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground">
      <Icon name="left" /> Customers / Preview
    </button>
  );

  if (!customer) {
    return (
      <Page>
        {back}
        {query.isPending ? (
          <p className="mt-8 text-muted-foreground">Loading customer…</p>
        ) : query.isError && !notFound ? (
          <div className="mt-8">
            <p className="text-muted-foreground">{query.error.message}</p>
            <Button variant="outline" size="sm" className="mt-3 rounded-lg" onClick={() => query.refetch()}>Try again</Button>
          </div>
        ) : (
          <p className="mt-8 text-muted-foreground">
            This customer could not be found. Go back to the customer list and pick another.
          </p>
        )}
      </Page>
    );
  }

  const name = fullName(customer);
  const pay = readPaymentStatus(customer.payment_status);
  const mode = readPaymentMode(customer.payment_mode);
  const docs = readDocStatus(customer.doc_status);
  const n = customer.services.length;

  return (
    <Page>
      {back}

      <div className="mt-6 flex items-center gap-4">
        <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-accent text-xl font-extrabold text-accent-foreground">
          {initials(name)}
        </span>
        <div className="min-w-0">
          <h1 className="truncate text-3xl font-extrabold tracking-tight">{name}</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <StatusPill tone="neutral">{n} {n === 1 ? "service" : "services"}</StatusPill>
            <StatusPill tone={pay.tone}>Payment: {pay.label}</StatusPill>
            {customer.status && customer.status !== "ACTIVE" && <StatusPill tone="warning">{customer.status}</StatusPill>}
          </div>
        </div>
      </div>

      <Section>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-bold">Customer details</h2>
          <Button variant="outline" className="rounded-xl" onClick={() => setEditing(true)} disabled={!customer.person_id}>
            <Icon name="pencil" /> Edit details
          </Button>
        </div>
        {editing && (
          <EditDetailsDialog open onClose={() => setEditing(false)} bdId={session.userId} customer={customer} />
        )}
        <dl className="mt-6 grid grid-cols-1 gap-x-6 gap-y-5 md:grid-cols-2">
          <Detail label="Full name">{name}</Detail>
          <Detail label="Mobile number">{phone(customer) || "Not added"}</Detail>
          <Detail label="Email ID" muted={!customer.email_address}>{customer.email_address || "Not added"}</Detail>
          <Detail label="Customer ID">{customer.person_id}</Detail>
          <Detail label="Address" wide muted={!address(customer)}>{address(customer) || "Not added"}</Detail>
        </dl>
      </Section>

      {customer.person_id && (
        <ServicesPayment bdId={session.userId} personId={customer.person_id} startAdding={params.get("edit") === "1"} />
      )}

      <Section>
        <h2 className="text-lg font-bold">Payment</h2>
        <dl className="mt-5 grid grid-cols-1 gap-x-6 gap-y-5 md:grid-cols-4">
          <Detail label="Status"><StatusPill tone={pay.tone}>{pay.label}</StatusPill></Detail>
          <Detail label="Mode" muted={!mode}>{mode ?? "Not chosen"}</Detail>
          <Detail label="Total paid">{payments.isPending ? "–" : inr(totalPaid)}</Detail>
          <Detail label="Documents"><StatusPill tone={docs.tone}>{docs.label}</StatusPill></Detail>
        </dl>
        <h3 className="mt-7 text-sm font-bold">Payment details by service</h3>
        <div className="mt-3 overflow-hidden rounded-2xl border">
          <PaymentsTable query={payments} viewer="bd" />
        </div>
      </Section>
    </Page>
  );
}

function Page({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-full bg-background px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-[980px]">{children}</div>
    </div>
  );
}

function Section({ children }: { children: ReactNode }) {
  return <section className="mt-6 rounded-3xl border bg-card p-6 first-of-type:mt-8 sm:p-7">{children}</section>;
}

function Detail({ label, children, wide, muted }: { label: string; children: ReactNode; wide?: boolean; muted?: boolean }) {
  return (
    <div className={cn(wide && "md:col-span-2")}>
      <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
      <dd className={cn("mt-1.5 text-sm font-medium", muted && "text-muted-foreground")}>{children}</dd>
    </div>
  );
}

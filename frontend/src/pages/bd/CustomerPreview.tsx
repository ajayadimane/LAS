import { useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useBdCustomer } from "@/api/bd";
import { ApiError } from "@/api/client";
import { useCustomerServices } from "@/api/services";
import { useSession } from "@/auth/AuthContext";
import { AnalysisReportCard } from "@/components/AnalysisReport";
import { PaymentsTable, usePayments } from "@/components/PaymentsTable";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { SectionCard } from "@/components/ui/SectionCard";
import { StepBadge, Stepper } from "@/components/ui/Stepper";
import { StatusPill } from "@/components/ui/StatusPill";
import { cn } from "@/lib/cn";
import { address, fullName, phone } from "@/lib/customer";
import { readDocsColumn, readPaymentMode, readPaymentStatus } from "@/lib/customerStatus";
import { initials, inr } from "@/lib/format";
import { paths } from "@/routes";
import { EditDetailsDialog } from "./EditDetails";
import { ServicesPayment } from "./ServicesPayment";

const STEPS = ["Customer details", "Services & payment", "Uploads", "Analysis"];
const clampStep = (n: number) => Math.min(STEPS.length, Math.max(1, Math.trunc(n)));

/**
 * One customer of the signed-in BD, as the same four steps as Add customer:
 * details, services & payment, uploads, analysis.
 *
 * The middleware returns 404 unless the person is registered under this BD.
 * Email and address are editable (EditDetails); name and mobile have no update API yet.
 */
export default function CustomerPreview() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  // The step is in the URL (?step=2), so a reload or a shared link opens the same step.
  // The dashboard's edit pencil (?edit=1) opens "Add services" on step 2.
  const step = clampStep(Number(params.get("step")) || (params.get("edit") === "1" ? 2 : 1));
  const go = (n: number, adding = false) => {
    setParams(adding ? { step: String(n), edit: "1" } : { step: String(n) }, { replace: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const session = useSession()!; // RequireRole guarantees a BD session
  const query = useBdCustomer(session.userId, id ?? "");
  const customer = query.data;
  const [editing, setEditing] = useState(false);
  const payments = useCustomerServices(session.userId, customer?.person_id ?? null);
  const { totalPaid } = usePayments(payments.data);
  const notFound = query.error instanceof ApiError && query.error.status === 404;

  const back = () => navigate(paths.bdDashboard);

  if (!customer) {
    return (
      <Page>
        <PageHeader title="Customer" onBack={back} backLabel="Back to customers" />
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
  const docs = readDocsColumn(customer.doc_status, customer.services);
  const n = customer.services.length;

  const paid = payments.data?.services.filter((sv) => sv.opted) ?? [];
  const withDocs = paid.filter((sv) => sv.document);
  // Real progress, not where the BD is looking: details are always done once registered.
  const completed = paid.length === 0 ? 1 : withDocs.length === 0 ? 2 : 3;

  return (
    <Page>
      <PageHeader
        onBack={back}
        backLabel="Back to customers"
        title={
          <span className="flex items-center gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-accent text-base font-extrabold text-accent-foreground">
              {initials(name)}
            </span>
            <span className="truncate">{name}</span>
          </span>
        }
        subtitle={
          <span className="mt-1 flex flex-wrap gap-2">
            <StatusPill tone="neutral">{n} {n === 1 ? "service" : "services"}</StatusPill>
            <StatusPill tone={pay.tone}>Payment: {pay.label}</StatusPill>
            {customer.status && customer.status !== "ACTIVE" && <StatusPill tone="warning">{customer.status}</StatusPill>}
          </span>
        }
        actions={<StepBadge current={step} total={STEPS.length} />}
      />
      {/* Every step of an existing customer can be opened; empty ones say what is missing. */}
      <Stepper className="mt-6" steps={STEPS} current={step} completed={completed} reachable={STEPS.length} onSelect={go} />

      {step === 1 && (
        <SectionCard className="mt-6" step={1} title="Customer details" subtitle="Personal identification and residential address."
          aside={
            <Button variant="outline-primary" className="rounded-xl" onClick={() => setEditing(true)} disabled={!customer.person_id}>
              <Icon name="pencil" /> Edit details
            </Button>
          }>
          {editing && (
            <EditDetailsDialog open onClose={() => setEditing(false)} bdId={session.userId} customer={customer} />
          )}
          <dl className="grid grid-cols-1 gap-x-6 gap-y-5 md:grid-cols-2">
            <Detail label="Full name">{name}</Detail>
            <Detail label="Mobile number">{phone(customer) || "Not added"}</Detail>
            <Detail label="Email ID" muted={!customer.email_address}>{customer.email_address || "Not added"}</Detail>
            <Detail label="Customer ID">{customer.person_id}</Detail>
            <Detail label="Address" wide muted={!address(customer)}>{address(customer) || "Not added"}</Detail>
          </dl>
        </SectionCard>
      )}

      {step === 2 && customer.person_id && (
        <>
          <div className="mt-6">
            <ServicesPayment bdId={session.userId} personId={customer.person_id} step={2} startAdding={params.get("edit") === "1"}
              onGoToUploads={() => go(3)} />
          </div>
          <SectionCard className="mt-6" icon="receipt" title="Payment Overview"
            subtitle="View payment status, transaction details and service-wise payment history.">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard label="Payment Status" tone={pay.bucket === "paid" ? "success" : pay.bucket === "pending" ? "warning" : "plain"} icon="checkcircle" value={<span className="text-2xl">{pay.label}</span>}
                caption={pay.bucket === "paid" ? "All selected charges settled" : undefined} />
              <KpiCard label="Payment Mode" tone="info" icon="clock" value={<span className="text-2xl">{mode ?? "Not chosen"}</span>}
                caption={mode === "Pay later" ? "Deferred billing terms" : undefined} />
              <KpiCard label="Total Paid" tone="primary" icon="rupee" value={payments.isPending ? "–" : inr(totalPaid)}
                caption="Includes one-time joining fee" />
              <KpiCard label="Documents" tone={docs.bucket === "not-uploaded" ? "danger" : docs.bucket === "done" ? "success" : "info"} icon="file"
                value={<StatusPill tone={docs.tone} className="bg-card text-sm">{docs.label}</StatusPill>}
                caption={docs.bucket === "not-uploaded" ? "Pending document submission" : undefined} />
            </div>
            <div className="mt-7 flex items-center justify-between gap-2">
              <h3 className="font-bold">Payment details by service</h3>
              {payments.data && (
                <span className="text-xs text-muted-foreground">
                  {payments.data.services.filter((sv) => sv.opted || sv.requested).length + (payments.data.first_payment ? 0 : 1)} items
                </span>
              )}
            </div>
            <div className="mt-3 overflow-hidden rounded-2xl border">
              <PaymentsTable query={payments} viewer="bd" />
            </div>
          </SectionCard>
        </>
      )}

      {step === 3 && customer.person_id && (
        <div className="mt-6">
          <ServicesPayment bdId={session.userId} personId={customer.person_id} step={3} view="uploads" />
        </div>
      )}

      {step === 4 && customer.person_id && (
        <div className="mt-6 space-y-5">
          {withDocs.length === 0 ? (
            <SectionCard step={4} title="Analysis" subtitle="Reports appear here once a document has been uploaded for a paid service." />
          ) : (
            withDocs.map((sv) => <AnalysisReportCard key={sv.code} bdId={session.userId} personId={customer.person_id!} service={sv} />)
          )}
        </div>
      )}

      <div className="mt-6 flex flex-wrap justify-end gap-3">
        {step > 1 && (
          <Button variant="outline" className="h-11 rounded-xl" onClick={() => go(step - 1)}>
            <Icon name="left" /> Back
          </Button>
        )}
        {step < STEPS.length && (
          <Button variant="outline-primary" className="h-11 rounded-xl" onClick={() => go(step + 1)}>
            Next: {STEPS[step]} <Icon name="right" />
          </Button>
        )}
        <Button className="h-11 rounded-xl" onClick={back}>Back to customers</Button>
      </div>
    </Page>
  );
}

function Page({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-full bg-background px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-[1040px]">{children}</div>
    </div>
  );
}

function Detail({ label, children, wide, muted }: { label: string; children: ReactNode; wide?: boolean; muted?: boolean }) {
  return (
    <div className={cn(wide && "md:col-span-2")}>
      <dt className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className={cn("mt-1.5 text-sm font-medium", muted && "text-muted-foreground")}>{children}</dd>
    </div>
  );
}

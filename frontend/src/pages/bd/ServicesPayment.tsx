import { useState } from "react";
import {
  documentPath, isOutcomeUnknown, useAuthorize, useCatalog, useCustomerServices, useDecline, usePayment, useUploadDocument,
  type CustomerService, type PaymentMode, type ServiceDetails,
} from "@/api/services";
import { ApiError } from "@/api/client";
import { DocumentPreview } from "@/components/DocumentPreview";
import { DetailsSummary, ServiceDetailsFields, detailsComplete, detailsPayload } from "@/components/ServiceDetailsFields";
import { Button } from "@/components/ui/Button";
import { Dropzone } from "@/components/ui/Dropzone";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Notice } from "@/components/ui/Notice";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusPill } from "@/components/ui/StatusPill";
import { useToast } from "@/components/ui/Toast";
import { UpiQr, upiLink } from "@/components/ui/UpiQr";
import { UPI_ID } from "@/data/catalog";
import { cn } from "@/lib/cn";
import { readPaymentMode, readPaymentStatus, readServiceStatus } from "@/lib/customerStatus";
import { inr, inrShort } from "@/lib/format";
import { serviceIcon } from "@/lib/serviceIcons";

/**
 * Step 2 of adding a customer: choose services, pay, upload documents.
 *
 * Pay now shows a QR (a placeholder until the real one is integrated); the BD
 * confirms receipt. Pay later needs both consents, then the middleware moves
 * the amount from the pool to the main account. Each service tracks its own
 * status, payment and document; a document unlocks once that service is paid.
 * What is opted and paid is read back from the customer record, so reopening
 * the page resumes where it stopped.
 */
export function ServicesPayment({ bdId, personId, step, view = "all", startAdding = false, onGoToUploads }: {
  bdId: string;
  personId: string | null;
  step?: number;
  /**
   * "all": opted services, customer requests and their documents (customer page; Add customer step 2).
   * "uploads": one upload panel per opted service (Add customer step 3).
   */
  view?: "all" | "uploads";
  /** Open "Add services" straight away (Edit customer). */
  startAdding?: boolean;
  /** "all" view: documents are uploaded on the Uploads step, so a service still needing one links there. */
  onGoToUploads?: () => void;
}) {
  const catalog = useCatalog();
  const state = useCustomerServices(bdId, personId);
  const locked = personId === null;
  const [adding, setAdding] = useState(startAdding);
  // Opted services, plus the customer's own requests waiting for this BD.
  const opted = state.data?.services.filter((s) => s.opted || s.requested) ?? [];
  const optedCodes = opted.map((s) => s.code);
  const requests = opted.filter((s) => s.requested).length;
  const inProgress = !!state.data?.payment_in_progress;
  const canAdd = !!state.data && !inProgress && state.data.services.some((s) => !s.opted);
  const uploads = view === "uploads";
  const paid = opted.filter((s) => !s.requested);

  const body = locked ? (
    <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
      <Icon name="lock" className="h-3.5 w-3.5" /> Save the customer first to choose services.
    </p>
  ) : catalog.isPending || state.isPending ? (
    <p className="text-sm text-muted-foreground">Loading…</p>
  ) : catalog.isError || state.isError ? (
    <Notice tone="error" className="mt-0">
      <p>{(catalog.error ?? state.error)?.message}</p>
      <Button size="sm" variant="outline" className="mt-2 rounded-lg" onClick={() => { catalog.refetch(); state.refetch(); }}>Try again</Button>
    </Notice>
  ) : uploads ? (
    paid.length === 0 ? (
      <p className="text-sm text-muted-foreground">No paid services yet. Choose and pay for a service first.</p>
    ) : (
      <div className="space-y-5">
        <UploadProgress services={paid} />
        {paid.map((s) => <UploadPanel key={s.code} bdId={bdId} personId={personId} service={s} />)}
      </div>
    )
  ) : opted.length === 0 ? (
    <ChooseAndPay bdId={bdId} personId={personId} opted={[]} firstPayment={state.data.first_payment} />
  ) : (
    <>
      {inProgress && (
        <Notice tone="warning" className="mb-5 mt-0">
          A payment for this customer was started but its result was not confirmed. Do not pay again: contact
          support to reconcile it first.
        </Notice>
      )}
      <ServiceRows bdId={bdId} personId={personId} services={opted} onGoToUploads={onGoToUploads} />
      {adding && canAdd && (
        <div className="mt-8 border-t pt-6">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-bold">Add services</h3>
            <Button size="sm" variant="ghost" className="rounded-lg" onClick={() => setAdding(false)}>Cancel</Button>
          </div>
          <ChooseAndPay bdId={bdId} personId={personId} opted={optedCodes} firstPayment={state.data.first_payment}
            onPaid={() => setAdding(false)} />
        </div>
      )}
    </>
  );

  // Services are added on the services step only, not from Uploads.
  const showAdd = !locked && !uploads && opted.length > 0 && canAdd && !adding;
  const addButton = showAdd && (
    <Button variant="outline-primary" className="h-11 rounded-xl" onClick={() => setAdding(true)}>
      <Icon name="plus" /> Add services
    </Button>
  );

  return (
    <SectionCard
      className={cn(locked && "opacity-60")}
      step={step}
      title={uploads ? "Documents & Uploads" : opted.length === 0 ? "Select Services" : "Services, payment & documents"}
      subtitle={
        uploads ? "Upload the required documents for the customer's selected services."
          : opted.length === 0 ? "Choose the advisory and verification services required for this customer."
          : "Review service requests requiring your authorization and inspect uploaded customer documents."
      }
      aside={
        uploads && paid.length > 0 ? (
          <StatusPill tone="success" icon="folder">{paid.length} Selected {paid.length === 1 ? "Service" : "Services"}</StatusPill>
        ) : requests > 0 ? (
          <StatusPill tone="warning" icon="alertcircle">{requests} Authorization Pending</StatusPill>
        ) : undefined
      }
      footer={addButton || undefined}
    >
      {body}
    </SectionCard>
  );
}

/**
 * Choose services and pay. `opted` services are hidden: they are paid for
 * already. The joining fee is charged on the first payment only.
 */
function ChooseAndPay({ bdId, personId, opted, firstPayment, onPaid }: {
  bdId: string;
  personId: string;
  opted: string[];
  firstPayment: boolean;
  onPaid?: () => void;
}) {
  const toast = useToast();
  const catalog = useCatalog().data!;
  const pay = usePayment(bdId, personId);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState<PaymentMode | null>(null);
  const [bdConsent, setBdConsent] = useState(false);
  const [customerConsent, setCustomerConsent] = useState(false);
  const [details, setDetails] = useState<ServiceDetails>({});

  const available = catalog.services.filter((s) => !opted.includes(s.code));
  const joiningFee = firstPayment ? catalog.joining_fee : 0;
  const chosen = available.filter((s) => selected.includes(s.code));
  const inHouse = chosen.filter((s) => !s.external);
  const total = inHouse.length ? inHouse.reduce((sum, s) => sum + s.fee, 0) + joiningFee : 0;
  const unknown = isOutcomeUnknown(pay.error);
  const busy = pay.isPending || unknown;
  const detailsDone = detailsComplete(inHouse, details);

  const toggle = (code: string) => {
    if (busy) return;
    setSelected((sel) => (sel.includes(code) ? sel.filter((c) => c !== code) : [...sel, code]));
  };

  const submit = () =>
    pay.mutate(
      {
        services: inHouse.map((s) => s.code),
        mode: mode!,
        bd_consent: bdConsent,
        customer_consent: customerConsent,
        details: detailsPayload(inHouse, details),
      },
      {
        onSuccess: (res) => {
          if (res.status_recorded) {
            toast(`${inr(res.amount)} ${res.mode === "pay_now" ? "payment recorded" : "paid from the pool"}`, "success");
          } else {
            toast("Payment made, but not recorded on the customer. Contact support; do not pay again.", "default");
          }
          onPaid?.();
        },
      },
    );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <fieldset disabled={busy} className="min-w-0 space-y-3">
        <legend className="sr-only">Services</legend>
        {available.map((s) => {
          const on = selected.includes(s.code);
          return (
            <div key={s.code} className={cn("rounded-2xl border bg-card transition-colors",
              on ? "border-primary bg-accent/60 ring-1 ring-primary/40" : "hover:border-primary/30")}>
              <label className="flex cursor-pointer items-start gap-3.5 p-4">
                <input type="checkbox" className="check mt-3" checked={on} onChange={() => toggle(s.code)} />
                <ServiceIconTile code={s.code} solid={on} />
                <div className="min-w-0 flex-1">
                  <p className="font-bold">{s.name}</p>
                  <p className="mt-0.5 text-sm leading-relaxed text-muted-foreground">{s.description}</p>
                  {s.external && (
                    <p className="mt-2 inline-flex items-center gap-1.5 rounded-lg border bg-muted/50 px-2.5 py-1 text-xs text-muted-foreground">
                      <Icon name="info" className="h-3.5 w-3.5" /> Documents and payment are handled on the{" "}
                      {s.portal_url ? <a className="font-semibold text-primary underline" href={s.portal_url} target="_blank" rel="noreferrer">partner portal</a> : "partner portal"}.
                    </p>
                  )}
                </div>
                <span className="shrink-0 text-base font-extrabold tabular-nums">{inrShort(s.fee)}</span>
              </label>
              {on && !s.external && s.details.length > 0 && (
                <div className="mx-4 border-t border-primary/20 pb-4 pt-4">
                  <ServiceDetailsFields service={s} values={details} onChange={setDetails} disabled={busy} />
                </div>
              )}
            </div>
          );
        })}
      </fieldset>

      <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
        <div className="rounded-2xl border bg-card p-5 shadow-card">
          <div className="flex items-center justify-between gap-2 border-b pb-3">
            <p className="flex items-center gap-2 font-bold"><Icon name="receipt" className="h-4 w-4 text-primary" /> Payment Summary</p>
            <StatusPill tone="success" icon="lock">Secure</StatusPill>
          </div>
          {inHouse.length === 0 ? (
            <p className="pt-4 text-sm text-muted-foreground">
              Select a service to see the total.{!firstPayment && " The joining fee has already been paid."}
            </p>
          ) : (
            <>
              <div className="space-y-2.5 pt-4">
                {inHouse.map((s) => <Line key={s.code} label={s.name} amount={inr(s.fee)} />)}
                {firstPayment && <Line label="One-time joining fee" amount={inr(joiningFee)} />}
                {chosen.filter((s) => s.external).map((s) => (
                  <Line key={s.code} label={`${s.name} (paid on partner portal)`} amount={inr(s.fee)} struck />
                ))}
              </div>
              <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-primary/20 bg-accent px-4 py-3">
                <span className="text-sm font-semibold">Total amount</span>
                <span className="text-xl font-extrabold tabular-nums text-accent-foreground">{inr(total)}</span>
              </div>
            </>
          )}
        </div>

        {inHouse.length > 0 && (
          <div className="rounded-2xl border bg-card p-5 shadow-card">
            <p className="font-bold">How will the customer pay?</p>
            <div className="mt-3 space-y-3">
              <ModeCard active={mode === "pay_now"} disabled={busy} onClick={() => setMode("pay_now")} icon="qr"
                title="Pay now" text="Customer scans a UPI QR and pays the full amount." />
              <ModeCard active={mode === "pay_later"} disabled={busy} onClick={() => setMode("pay_later")} icon="wallet"
                title="Pay later" text="LAS pays from the pool now; the customer repays later." />
            </div>

            {!detailsDone && (
              <p className="mt-4 flex items-start gap-2 rounded-xl border border-warning-foreground/20 bg-warning/60 px-3 py-2.5 text-xs font-medium text-warning-foreground">
                <Icon name="alert" className="mt-px h-3.5 w-3.5 shrink-0" /> Fill in the details asked for above to continue.
              </p>
            )}

            {mode === "pay_now" && (
              <div className="mt-4 flex flex-col items-center gap-3 rounded-xl bg-muted/50 p-4 text-center">
                <UpiQr text={upiLink(UPI_ID, total, `LAS services ${personId}`)} size={170} />
                <p className="text-sm font-semibold">{inr(total)} to {UPI_ID}</p>
                <p className="text-xs text-muted-foreground">Sample QR for now: no real payment is collected yet.</p>
                <Button className="mt-1 w-full rounded-xl" disabled={busy || !detailsDone} onClick={submit}>
                  {pay.isPending ? "Recording…" : <><Icon name="check" /> Payment received</>}
                </Button>
              </div>
            )}

            {mode === "pay_later" && (
              <div className="mt-4 space-y-3 rounded-xl bg-muted/50 p-4">
                <Consent checked={bdConsent} disabled={busy} onChange={setBdConsent}>
                  I know the customer, and I stand as guarantor for their service fee. {inr(total)}
                </Consent>
                <Consent checked={customerConsent} disabled={busy} onChange={setCustomerConsent}>
                  The customer has promised to pay these service charges of {inr(total)}.
                </Consent>
                <Button className="w-full rounded-xl" disabled={busy || !bdConsent || !customerConsent || !detailsDone} onClick={submit}>
                  {pay.isPending ? "Processing payment…" : `Confirm pay later (${inr(total)})`}
                </Button>
              </div>
            )}
          </div>
        )}

        {pay.isError && (
          <Notice tone={unknown ? "warning" : "error"} className="mt-0">
            <p>{pay.error.message}</p>
            {pay.error instanceof ApiError && unknown && <Reference body={pay.error.body} />}
          </Notice>
        )}
      </aside>
    </div>
  );
}

interface RowProps {
  bdId: string;
  personId: string;
  service: CustomerService;
  onGoToUploads?: () => void;
}

function ServiceRows({ services, ...rest }: Omit<RowProps, "service"> & { services: CustomerService[] }) {
  return (
    <ul className="space-y-5">
      {services.map((s) => <ServiceRow key={s.code} service={s} {...rest} />)}
    </ul>
  );
}

/** One opted service: where it is, how it was paid, and its document. */
function ServiceRow(props: RowProps) {
  if (props.service.requested) return <RequestRow {...props} />;
  return <OptedRow {...props} />;
}

/**
 * A service the customer asked for themselves. Pay later: the BD stands
 * guarantor and the pool transfer runs. Pay now: the BD confirms the QR
 * payment arrived. Or the BD declines.
 */
function RequestRow({ bdId, personId, service }: RowProps) {
  const toast = useToast();
  const authorize = useAuthorize(bdId, personId);
  const decline = useDecline(bdId, personId);
  const [guarantee, setGuarantee] = useState(false);
  const later = readPaymentMode(service.payment_mode) === "Pay later";
  const busy = authorize.isPending || decline.isPending || isOutcomeUnknown(authorize.error);

  const approve = () =>
    authorize.mutate(
      { code: service.code, bdConsent: later && guarantee },
      { onSuccess: (res) => toast(`${service.name} authorized (${inr(res.amount)})`, "success") },
    );

  return (
    <li className="rounded-2xl border border-warning-foreground/25 bg-warning/40 p-5 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <ServiceIconTile code={service.code} tone="warning" />
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 font-bold">
              {service.name} <Icon name="alertcircle" className="h-4 w-4 text-warning-foreground" />
            </p>
            <StatusPill tone="warning" className="mt-1">Customer request · {later ? "Pay later" : "Pay now"}</StatusPill>
            <DetailsSummary details={service.details} className="mt-1.5 text-xs text-muted-foreground" />
          </div>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted-foreground">Service fee</p>
          <p className="text-xl font-extrabold tabular-nums">{inrShort(service.fee)}</p>
        </div>
      </div>
      <div className="mt-4 border-t border-warning-foreground/15 pt-4">
        {later ? (
          <div className="rounded-xl border border-warning-foreground/15 bg-card px-4 py-3">
            <Consent checked={guarantee} disabled={busy} onChange={setGuarantee}>
              I know the customer, and I stand as guarantor for their service fee.
            </Consent>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Authorize once you have confirmed the customer's QR payment arrived.</p>
        )}
        <div className="mt-4 flex flex-wrap gap-2.5">
          <Button className="h-11 rounded-xl" disabled={busy || (later && !guarantee)} onClick={approve}>
            <Icon name="shieldcheck" /> {authorize.isPending ? "Authorizing…" : later ? "Authorize & stand guarantor" : "Payment received"}
          </Button>
          <Button variant="outline" className="h-11 rounded-xl" disabled={busy}
            onClick={() => decline.mutate(service.code, { onSuccess: () => toast(`${service.name} request declined`, "default") })}>
            {decline.isPending ? "Declining…" : "Decline"}
          </Button>
        </div>
        {later && !guarantee && !busy && (
          <p className="mt-2 text-xs text-muted-foreground">Select the guarantor checkbox above to enable authorization.</p>
        )}
        {(authorize.isError || decline.isError) && (
          <p className="mt-2 text-xs text-destructive">{(authorize.error ?? decline.error)?.message}</p>
        )}
      </div>
    </li>
  );
}

/** Upload state shared by the customer-page row and the Add customer upload panel. */
/** One upload per service: once a document is in, it can only be re-uploaded if it was rejected. */
const canUploadDocument = (service: CustomerService) =>
  service.can_upload && (!service.document || (service.status ?? "").trim().toUpperCase() === "REJECTED");

function useServiceUpload(bdId: string, personId: string, service: CustomerService) {
  const toast = useToast();
  const upload = useUploadDocument(bdId, personId);
  const canUpload = canUploadDocument(service);
  const onFile = (file: File) =>
    upload.mutate({ code: service.code, file }, { onSuccess: () => toast(`${service.name}: document uploaded`, "success") });
  return { upload, canUpload, onFile };
}

/** An opted service on the Services step: status, payment and its document (read-only; uploading is the Uploads step). */
function OptedRow({ bdId, personId, service, onGoToUploads }: RowProps) {
  const status = readServiceStatus(service.status);
  const pay = readPaymentStatus(service.payment_status);
  const mode = readPaymentMode(service.payment_mode);

  return (
    <li className="rounded-2xl border border-primary/20 bg-accent/50 p-5">
      <div className="flex min-w-0 items-start gap-3">
        <ServiceIconTile code={service.code} />
        <div className="min-w-0">
          <p className="font-bold">{service.name}</p>
          <div className="mt-1 flex flex-wrap gap-2">
            <StatusPill tone={status.tone}>{status.label}</StatusPill>
            <StatusPill tone={pay.tone} icon={pay.bucket === "paid" ? "checkcircle" : undefined}>{pay.label}{mode ? ` · ${mode}` : ""}</StatusPill>
          </div>
          <DetailsSummary details={service.details} className="mt-1.5 text-xs text-muted-foreground" />
          {service.transaction_id && (
            <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">Ref {service.transaction_id}</p>
          )}
        </div>
      </div>
      <div className="mt-4 border-t border-primary/15 pt-4">
        <DocumentRow bdId={bdId} personId={personId} service={service}
          action={canUploadDocument(service) && onGoToUploads && (
            <Button size="sm" variant="outline-primary" className="rounded-lg" onClick={onGoToUploads}>
              <Icon name="upload" /> {service.document ? "Re-upload in Uploads" : "Go to Uploads"} <Icon name="right" className="h-3.5 w-3.5" />
            </Button>
          )} />
      </div>
    </li>
  );
}

/** The service's document as a white file row with Preview, or "No document yet". */
function DocumentRow({ bdId, personId, service, action }: {
  bdId: string; personId: string; service: CustomerService; action?: React.ReactNode;
}) {
  const [previewing, setPreviewing] = useState(false);
  const doc = service.document;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-card px-4 py-3">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
        <Icon name={doc ? "file" : "minuscircle"} />
      </span>
      <p className="min-w-0 flex-1 text-sm">
        {doc ? (
          <>
            {isRejected(service)
              ? <Icon name="alertcircle" className="mr-1.5 inline h-4 w-4 text-destructive" />
              : <Icon name="checkcircle" className="mr-1.5 inline h-4 w-4 text-primary" />}
            <span className="font-semibold">{doc.name}</span>{" "}
            <span className="text-xs text-muted-foreground">({Math.ceil(doc.size_bytes / 1024)} KB)</span>
          </>
        ) : (
          <span className="text-muted-foreground">No document yet</span>
        )}
      </p>
      {doc && (
        <>
          <Button size="sm" variant="outline-primary" className="rounded-lg" onClick={() => setPreviewing(true)}>
            <Icon name="eye" /> Preview
          </Button>
          <DocumentPreview path={previewing ? documentPath(bdId, personId, service.code) : null} name={doc.name}
            onClose={() => setPreviewing(false)} />
        </>
      )}
      {action}
    </div>
  );
}

type UploadState = "needed" | "rejected" | "review" | "done" | "closed";

const isRejected = (s: CustomerService) => (s.status ?? "").trim().toUpperCase() === "REJECTED";

/** Where a service's document stands, from the BD's point of view: what, if anything, they must do. */
function uploadState(s: CustomerService): UploadState {
  if (isRejected(s) && s.can_upload) return "rejected";
  if (!s.document) return s.can_upload ? "needed" : "closed";
  return s.can_upload ? "review" : "done";
}

const UPLOAD_BADGE: Record<UploadState, { label: string; tone: "warning" | "danger" | "info" | "success" | "neutral"; icon: IconName }> = {
  needed: { label: "Document needed", tone: "warning", icon: "upload" },
  rejected: { label: "Rejected: re-upload", tone: "danger", icon: "alertcircle" },
  review: { label: "Uploaded · in review", tone: "info", icon: "clock" },
  done: { label: "Complete", tone: "success", icon: "checkcircle" },
  closed: { label: "Uploads closed", tone: "neutral", icon: "lock" },
};

/** "2 of 3 documents uploaded", a bar, and one chip per service that jumps to its card. */
function UploadProgress({ services }: { services: CustomerService[] }) {
  const pending = services.filter((s) => ["needed", "rejected"].includes(uploadState(s)));
  const done = services.length - pending.length;
  const pct = Math.round((done / services.length) * 100);
  return (
    <div className="rounded-2xl border bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-bold">
          {pending.length === 0 ? "All documents are in" : `${done} of ${services.length} documents uploaded`}
        </p>
        <p className="text-xs text-muted-foreground">
          {pending.length === 0 ? "Nothing left to upload." : `${pending.length} still to upload: one file per service.`}
        </p>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
        aria-label="Documents uploaded">
        <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      {services.length > 1 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {services.map((s) => {
            const ok = !pending.includes(s);
            return (
              <a key={s.code} href={`#upload-${s.code}`}
                onClick={(e) => { e.preventDefault(); document.getElementById(`upload-${s.code}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}
                className={cn("flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition-colors hover:bg-accent",
                  ok ? "border-primary/30 bg-card text-accent-foreground" : "border-warning-foreground/30 bg-warning/40 text-warning-foreground")}>
                <Icon name={ok ? "checkcircle" : "upload"} className="h-3.5 w-3.5" /> {s.name}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Add customer step 3: one paid service, its key facts, and either the drop area or its document. */
function UploadPanel({ bdId, personId, service }: { bdId: string; personId: string; service: CustomerService }) {
  const catalog = useCatalog();
  const { upload, onFile } = useServiceUpload(bdId, personId, service);
  const pay = readPaymentStatus(service.payment_status);
  const mode = readPaymentMode(service.payment_mode);
  const description = catalog.data?.services.find((c) => c.code === service.code)?.description;
  const state = uploadState(service);
  const badge = UPLOAD_BADGE[state];
  const facts: Array<[string, React.ReactNode]> = [
    ...service.details.filter((d) => d.value).map((d): [string, React.ReactNode] => [d.label, d.value]),
    [mode ? `${pay.label} · ${mode}` : pay.label, service.transaction_id ? <span className="font-mono">Ref {service.transaction_id}</span> : null],
  ];

  return (
    <section id={`upload-${service.code}`} className={cn("scroll-mt-6 rounded-2xl border p-5",
      state === "needed" || state === "rejected" ? "border-primary/30 bg-card shadow-card" : "border-primary/15 bg-accent/40")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <ServiceIconTile code={service.code} />
          <div className="min-w-0">
            <h3 className="text-base font-bold">{service.name}</h3>
            {description && <p className="text-xs text-muted-foreground">{description}</p>}
            <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {facts.map(([label, value]) => (
                <span key={label} className="flex items-center gap-1">
                  <span className="font-semibold text-foreground">{label}</span>{value && <> {value}</>}
                </span>
              ))}
            </p>
          </div>
        </div>
        <StatusPill tone={badge.tone} icon={badge.icon}>{badge.label}</StatusPill>
      </div>

      <div className="mt-4 space-y-3">
        {state === "rejected" && (
          <Notice tone="warning" className="mt-0">
            This document was rejected. Upload a corrected file below; it replaces the current one.
          </Notice>
        )}
        {service.document && <DocumentRow bdId={bdId} personId={personId} service={service} />}
        {(state === "needed" || state === "rejected") && (
          <Dropzone onFile={onFile} busy={upload.isPending} error={upload.isError ? upload.error.message : null}
            title={state === "rejected" ? "Drop the corrected document here" : `Drop the ${service.name} document here`} />
        )}
        {state === "review" && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Icon name="info" className="h-3.5 w-3.5" /> Uploaded and waiting for review. You can upload again only if it is rejected.
          </p>
        )}
        {state === "closed" && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Icon name="lock" className="h-3.5 w-3.5" /> Uploads open once this service is paid.
          </p>
        )}
      </div>
    </section>
  );
}

/** Square icon tile for a service; `solid` = selected (primary fill). */
function ServiceIconTile({ code, solid, tone }: { code: string; solid?: boolean; tone?: "warning" }) {
  return (
    <span className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border",
      solid ? "border-primary bg-primary text-primary-foreground"
        : tone === "warning" ? "border-warning-foreground/20 bg-card text-warning-foreground"
        : "border-primary/15 bg-card text-accent-foreground")}>
      <Icon name={serviceIcon(code)} className="h-5 w-5" />
    </span>
  );
}

function ModeCard({ active, disabled, onClick, title, text, icon }: {
  active: boolean; disabled: boolean; onClick: () => void; title: string; text: string; icon: IconName;
}) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} aria-pressed={active}
      className={cn("flex w-full items-start gap-3 rounded-xl border p-3.5 text-left transition-colors disabled:opacity-60",
        active ? "border-primary bg-accent ring-1 ring-primary" : "bg-muted/30 hover:bg-muted/60")}>
      <span className={cn("mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2",
        active ? "border-primary" : "border-muted-foreground/50")} aria-hidden="true">
        {active && <span className="h-2 w-2 rounded-full bg-primary" />}
      </span>
      <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
        active ? "bg-primary text-primary-foreground" : "border bg-card text-foreground")}>
        <Icon name={icon} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-bold">{title}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{text}</span>
      </span>
    </button>
  );
}

export function Consent({ checked, disabled, onChange, children }: { checked: boolean; disabled: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type="checkbox" className="check mt-0.5" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}</span>
    </label>
  );
}

function Reference({ body }: { body: unknown }) {
  const err = (body as { error?: { transaction_id?: string | null; correlation_id?: string | null } } | null)?.error;
  if (!err?.transaction_id && !err?.correlation_id) return null;
  return (
    <p className="mt-2 font-mono text-xs">
      Reference: {err.transaction_id ?? "–"} / {err.correlation_id ?? "–"}
    </p>
  );
}

function Line({ label, amount, struck }: { label: string; amount: string; struck?: boolean }) {
  return (
    <div className={cn("flex justify-between gap-3 text-sm", struck && "text-muted-foreground")}>
      <span className={struck ? "" : "text-muted-foreground"}>{label}</span>
      <span className={cn("tabular-nums", struck ? "line-through decoration-1" : "font-semibold")}>{amount}</span>
    </div>
  );
}

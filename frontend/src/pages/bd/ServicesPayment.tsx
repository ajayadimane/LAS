import { useRef, useState } from "react";
import {
  documentPath, isOutcomeUnknown, useAuthorize, useCatalog, useCustomerServices, useDecline, usePayment, useUploadDocument,
  type CustomerService, type PaymentMode,
} from "@/api/services";
import { ApiError } from "@/api/client";
import { DocumentPreview } from "@/components/DocumentPreview";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Notice } from "@/components/ui/Notice";
import { StatusPill } from "@/components/ui/StatusPill";
import { useToast } from "@/components/ui/Toast";
import { UpiQr, upiLink } from "@/components/ui/UpiQr";
import { UPI_ID } from "@/data/catalog";
import { cn } from "@/lib/cn";
import { readPaymentMode, readPaymentStatus, readServiceStatus } from "@/lib/customerStatus";
import { inr, inrShort } from "@/lib/format";

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
export function ServicesPayment({ bdId, personId, step, startAdding = false }: {
  bdId: string;
  personId: string | null;
  step?: number;
  /** Open "Add services" straight away (Edit customer). */
  startAdding?: boolean;
}) {
  const catalog = useCatalog();
  const state = useCustomerServices(bdId, personId);
  const locked = personId === null;
  const [adding, setAdding] = useState(startAdding);
  // Opted services, plus the customer's own requests waiting for this BD.
  const opted = state.data?.services.filter((s) => s.opted || s.requested) ?? [];
  const optedCodes = opted.map((s) => s.code);
  const inProgress = !!state.data?.payment_in_progress;
  const canAdd = !!state.data && !inProgress && state.data.services.some((s) => !s.opted);

  return (
    <section className={cn("mt-6 rounded-3xl border bg-card p-6 sm:p-7", locked && "opacity-60")}>
      <div className="flex items-center gap-3">
        {step !== undefined && (
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
            {opted.length > 0 ? <Icon name="check" className="h-4 w-4" /> : step}
          </span>
        )}
        <h2 className="text-lg font-bold">Services, payment & documents</h2>
      </div>

      {locked ? (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-muted-foreground">
          <Icon name="lock" className="h-3.5 w-3.5" /> Save the customer first to choose services.
        </p>
      ) : catalog.isPending || state.isPending ? (
        <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
      ) : catalog.isError || state.isError ? (
        <Notice tone="error">
          <p>{(catalog.error ?? state.error)?.message}</p>
          <Button size="sm" variant="outline" className="mt-2 rounded-lg" onClick={() => { catalog.refetch(); state.refetch(); }}>Try again</Button>
        </Notice>
      ) : opted.length === 0 ? (
        <ChooseAndPay bdId={bdId} personId={personId} opted={[]} firstPayment={state.data.first_payment} />
      ) : (
        <>
          {inProgress && (
            <Notice tone="warning">
              A payment for this customer was started but its result was not confirmed. Do not pay again: contact
              support to reconcile it first.
            </Notice>
          )}
          <ServiceRows bdId={bdId} personId={personId} services={opted} />
          {adding && canAdd ? (
            <div className="mt-8 border-t pt-6">
              <div className="flex items-center justify-between gap-3">
                <h3 className="font-bold">Add services</h3>
                <Button size="sm" variant="ghost" className="rounded-lg" onClick={() => setAdding(false)}>Cancel</Button>
              </div>
              <ChooseAndPay bdId={bdId} personId={personId} opted={optedCodes} firstPayment={state.data.first_payment}
                onPaid={() => setAdding(false)} />
            </div>
          ) : canAdd ? (
            <Button variant="outline" className="mt-5 rounded-xl" onClick={() => setAdding(true)}>
              <Icon name="plus" /> Add services
            </Button>
          ) : null}
        </>
      )}
    </section>
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

  const available = catalog.services.filter((s) => !opted.includes(s.code));
  const joiningFee = firstPayment ? catalog.joining_fee : 0;
  const chosen = available.filter((s) => selected.includes(s.code));
  const inHouse = chosen.filter((s) => !s.external);
  const total = inHouse.length ? inHouse.reduce((sum, s) => sum + s.fee, 0) + joiningFee : 0;
  const unknown = isOutcomeUnknown(pay.error);
  const busy = pay.isPending || unknown;

  const toggle = (code: string) => {
    if (busy) return;
    setSelected((sel) => (sel.includes(code) ? sel.filter((c) => c !== code) : [...sel, code]));
  };

  const submit = () =>
    pay.mutate(
      { services: inHouse.map((s) => s.code), mode: mode!, bd_consent: bdConsent, customer_consent: customerConsent },
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
    <>
      <p className="mt-3 text-sm text-muted-foreground">
        Select services, then choose how the customer pays.
        {!firstPayment && " The joining fee has already been paid."}
      </p>

      <fieldset disabled={busy} className="mt-5 divide-y rounded-xl border">
        {available.map((s) => {
          const on = selected.includes(s.code);
          return (
            <label key={s.code} className={cn("flex cursor-pointer items-start gap-3 px-4 py-3.5 hover:bg-muted/50", on && "bg-accent/50")}>
              <input type="checkbox" className="check mt-0.5" checked={on} onChange={() => toggle(s.code)} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-bold">{s.name}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{s.description}</p>
                {s.external && (
                  <p className="mt-1 text-xs text-info-foreground">
                    Documents and payment are handled on the{" "}
                    {s.portal_url ? <a className="underline" href={s.portal_url} target="_blank" rel="noreferrer">partner portal</a> : "partner portal"}.
                  </p>
                )}
              </div>
              <span className="shrink-0 text-sm font-bold tabular-nums">{inrShort(s.fee)}</span>
            </label>
          );
        })}
      </fieldset>

      {inHouse.length > 0 && (
        <div className="mt-6 rounded-2xl border">
          <div className="space-y-2.5 p-5">
            {inHouse.map((s) => <Line key={s.code} label={s.name} amount={inr(s.fee)} />)}
            {firstPayment && <Line label="One-time joining fee" amount={inr(joiningFee)} />}
            {chosen.filter((s) => s.external).map((s) => (
              <Line key={s.code} label={`${s.name} (paid on partner portal)`} amount={inr(s.fee)} struck />
            ))}
          </div>
          <div className="flex items-center justify-between gap-3 border-t px-5 py-4">
            <span className="font-bold">Total amount</span>
            <span className="text-xl font-extrabold tabular-nums">{inr(total)}</span>
          </div>

          <div className="border-t p-5">
            <p className="text-sm font-bold">How will the customer pay?</p>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <ModeCard active={mode === "pay_now"} disabled={busy} onClick={() => setMode("pay_now")}
                title="Pay now" text="Customer scans a UPI QR and pays the full amount." />
              <ModeCard active={mode === "pay_later"} disabled={busy} onClick={() => setMode("pay_later")}
                title="Pay later" text="LAS pays from the pool now; the customer repays later." />
            </div>

            {mode === "pay_now" && (
              <div className="mt-5 flex flex-col items-center gap-3 rounded-xl bg-muted/50 p-5 text-center">
                <UpiQr text={upiLink(UPI_ID, total, `LAS services ${personId}`)} size={180} />
                <p className="text-sm font-semibold">{inr(total)} to {UPI_ID}</p>
                <p className="text-xs text-muted-foreground">Sample QR for now: no real payment is collected yet.</p>
                <Button className="mt-1 rounded-xl" disabled={busy} onClick={submit}>
                  {pay.isPending ? "Recording…" : <><Icon name="check" /> Payment received</>}
                </Button>
              </div>
            )}

            {mode === "pay_later" && (
              <div className="mt-5 space-y-3 rounded-xl bg-muted/50 p-5">
                <Consent checked={bdConsent} disabled={busy} onChange={setBdConsent}>
                  I know the customer, and I stand as guarantor for their service fee. {inr(total)} 
                </Consent>
                <Consent checked={customerConsent} disabled={busy} onChange={setCustomerConsent}>
                  TThe customer has promised to pay these service charges of {inr(total)}.
                </Consent>
                <Button className="rounded-xl" disabled={busy || !bdConsent || !customerConsent} onClick={submit}>
                  {pay.isPending ? "Processing payment…" : `Confirm pay later (${inr(total)})`}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {pay.isError && (
        <Notice tone={unknown ? "warning" : "error"}>
          <p>{pay.error.message}</p>
          {pay.error instanceof ApiError && unknown && <Reference body={pay.error.body} />}
        </Notice>
      )}
    </>
  );
}

function ServiceRows({ bdId, personId, services }: { bdId: string; personId: string; services: CustomerService[] }) {
  return (
    <>
      <ul className="mt-5 divide-y rounded-xl border">
        {services.map((s) => <ServiceRow key={s.code} bdId={bdId} personId={personId} service={s} />)}
      </ul>
      <p className="mt-2 text-xs text-muted-foreground">Documents: PDF, JPEG or PNG, up to 2 MB each.</p>
    </>
  );
}

/** One opted service: where it is, how it was paid, and its document. */
function ServiceRow({ bdId, personId, service }: { bdId: string; personId: string; service: CustomerService }) {
  if (service.requested) return <RequestRow bdId={bdId} personId={personId} service={service} />;
  return <OptedRow bdId={bdId} personId={personId} service={service} />;
}

/**
 * A service the customer asked for themselves. Pay later: the BD stands
 * guarantor and the pool transfer runs. Pay now: the BD confirms the QR
 * payment arrived. Or the BD declines.
 */
function RequestRow({ bdId, personId, service }: { bdId: string; personId: string; service: CustomerService }) {
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
    <li className="bg-warning/30 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-bold">{service.name}</p>
        <StatusPill tone="warning">Customer request · {later ? "Pay later" : "Pay now"}</StatusPill>
        <span className="text-xs text-muted-foreground">Fee {inrShort(service.fee)}</span>
      </div>
      {later ? (
        <div className="mt-3">
          <Consent checked={guarantee} disabled={busy} onChange={setGuarantee}>
            I know the customer, and I stand as guarantor for their service fee.
          </Consent>
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">Authorize once you have confirmed the customer's QR payment arrived.</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" className="rounded-lg" disabled={busy || (later && !guarantee)} onClick={approve}>
          {authorize.isPending ? "Authorizing…" : later ? "Authorize & stand guarantor" : "Payment received"}
        </Button>
        <Button size="sm" variant="outline" className="rounded-lg" disabled={busy}
          onClick={() => decline.mutate(service.code, { onSuccess: () => toast(`${service.name} request declined`, "default") })}>
          {decline.isPending ? "Declining…" : "Decline"}
        </Button>
      </div>
      {(authorize.isError || decline.isError) && (
        <p className="mt-2 text-xs text-destructive">{(authorize.error ?? decline.error)?.message}</p>
      )}
    </li>
  );
}

function OptedRow({ bdId, personId, service }: { bdId: string; personId: string; service: CustomerService }) {
  const toast = useToast();
  const upload = useUploadDocument(bdId, personId);
  const input = useRef<HTMLInputElement>(null);
  const [previewing, setPreviewing] = useState(false);
  const doc = service.document;
  const status = readServiceStatus(service.status);
  const pay = readPaymentStatus(service.payment_status);
  const mode = readPaymentMode(service.payment_mode);

  const onFile = (file: File | undefined) => {
    if (!file) return;
    upload.mutate({ code: service.code, file }, { onSuccess: () => toast(`${service.name}: document uploaded`, "success") });
    if (input.current) input.current.value = "";
  };

  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-bold">{service.name}</p>
          <StatusPill tone={status.tone}>{status.label}</StatusPill>
          <StatusPill tone={pay.tone}>{pay.label}{mode ? ` · ${mode}` : ""}</StatusPill>
        </div>
        <p className={cn("mt-1 text-xs", doc ? "text-success-foreground" : "text-muted-foreground")}>
          {doc ? <><Icon name="check" className="inline h-3 w-3" /> {doc.name} ({Math.ceil(doc.size_bytes / 1024)} KB)</> : "No document yet"}
        </p>
        {service.transaction_id && (
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">Ref {service.transaction_id}</p>
        )}
        {upload.isError && <p className="mt-1 text-xs text-destructive">{upload.error.message}</p>}
      </div>
      {doc && (
        <>
          <Button size="sm" variant="outline" className="rounded-lg" onClick={() => setPreviewing(true)}>
            <Icon name="eye" /> Preview
          </Button>
          <DocumentPreview path={previewing ? documentPath(bdId, personId, service.code) : null} name={doc.name}
            onClose={() => setPreviewing(false)} />
        </>
      )}
      {service.can_upload && (
        <>
          <input ref={input} type="file" accept="application/pdf,image/jpeg,image/png" className="hidden"
            onChange={(e) => onFile(e.target.files?.[0])} />
          <Button size="sm" variant={doc ? "outline" : "default"} className="rounded-lg" disabled={upload.isPending}
            onClick={() => input.current?.click()}>
            <Icon name="upload" /> {upload.isPending ? "Uploading…" : doc ? "Replace" : "Upload"}
          </Button>
        </>
      )}
    </li>
  );
}

function ModeCard({ active, disabled, onClick, title, text }: { active: boolean; disabled: boolean; onClick: () => void; title: string; text: string }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} aria-pressed={active}
      className={cn("rounded-xl border p-4 text-left transition-colors disabled:opacity-60",
        active ? "border-primary bg-accent/60 ring-1 ring-primary" : "hover:bg-muted/50")}>
      <p className="text-sm font-bold">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{text}</p>
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

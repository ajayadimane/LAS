import { useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { downloadMyDocument, useMyProfile, useMyServices, useRequestServices, useUploadMyDocument } from "@/api/customer";
import { useCatalog, type CatalogService, type CustomerService, type PaymentMode } from "@/api/services";
import { useWalletBalance } from "@/api/wallet";
import { useSession } from "@/auth/AuthContext";
import { clearSession } from "@/auth/session";
import { AppSidebar, type NavItem } from "@/components/layout/AppSidebar";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icon } from "@/components/ui/Icon";
import { StatCard } from "@/components/ui/StatCard";
import { StatusPill, type Tone } from "@/components/ui/StatusPill";
import { useToast } from "@/components/ui/Toast";
import { UpiQr, upiLink } from "@/components/ui/UpiQr";
import { UPI_ID } from "@/data/catalog";
import { cn } from "@/lib/cn";
import { address, fullName, phone } from "@/lib/customer";
import { readPaymentMode } from "@/lib/customerStatus";
import { inr, inrShort } from "@/lib/format";
import { paths } from "@/routes";

/**
 * The customer's dashboard, laid out as in the demo ("Customer dashboard").
 *
 * Choosing a service with Pay later sends a guarantee request to the BD;
 * nothing is charged until the BD stands guarantor. While the QR is a
 * placeholder, Pay now also waits for the BD to confirm the payment arrived.
 * Feedback & grievance and Billing have no backend yet.
 */

const STEPS = ["Upload", "Approval", "Analysis", "Report"];
const NOT_YET = "Not available yet";

/** How far along the four steps a service is, from its lifecycle field. */
const PROGRESS: Record<string, number> = { OPTED: 0, DOCS_UPLOADED: 1, APPROVED: 2, REPORT_READY: 4 };

const statusKey = (raw: string | null) => (raw ?? "").trim().toUpperCase().replace(/[\s-]+/g, "_");
const isPayLater = (s: CustomerService) => readPaymentMode(s.payment_mode) === "Pay later";
const isRejected = (s: CustomerService) => statusKey(s.status) === "REJECTED";

function serviceStatus(s: CustomerService): { label: string; tone: Tone } {
  if (s.requested) {
    return isPayLater(s)
      ? { label: "Waiting for BD guarantee", tone: "warning" }
      : { label: "Waiting for BD to confirm payment", tone: "warning" };
  }
  switch (statusKey(s.status)) {
    case "OPTED": return { label: "Upload documents", tone: "neutral" };
    case "DOCS_UPLOADED": return { label: "Awaiting report", tone: "info" };
    case "APPROVED": return { label: "Analysis in progress", tone: "info" };
    case "REJECTED": return { label: "Documents rejected: upload again", tone: "warning" };
    case "REPORT_READY": return { label: "Report ready", tone: "success" };
    default: return { label: s.status ?? "Not started", tone: "neutral" };
  }
}

/** The line under the service name: fee and how it is being paid. */
function payLabel(s: CustomerService) {
  if (s.requested) return isPayLater(s) ? "Pay later" : "Pay now";
  if (statusKey(s.payment_status) === "PROCESSING") return "Payment processing";
  return isPayLater(s) ? "Pay later" : "Paid";
}

export default function CustomerHome() {
  const navigate = useNavigate();
  const session = useSession()!; // RequireRole guarantees a customer session
  const profile = useMyProfile();
  const state = useMyServices();
  const wallet = useWalletBalance(session.userId);
  // Other customer pages open the Wallet and Profile dialogs via ?open=.
  const [params] = useSearchParams();
  const [dialog, setDialog] = useState<"new" | "profile" | "account" | null>(() => {
    const open = params.get("open");
    return open === "wallet" ? "account" : open === "profile" ? "profile" : null;
  });

  const mine = state.data?.services.filter((s) => s.opted || s.requested) ?? [];
  const awaitingReport = mine.filter((s) => statusKey(s.status) === "DOCS_UPLOADED").length;
  // Pay later services: LAS paid from the pool and the customer owes it back.
  const paymentDue = mine.filter((s) => s.opted && isPayLater(s)).length;
  const stat = (n: number) => (state.isPending ? "–" : n);

  const p = profile.data;
  const name = p ? fullName(p) : "";
  const balance = wallet.isPending ? "–" : wallet.isError || wallet.data.available_amount === null ? "Unavailable" : inr(wallet.data.available_amount);

  const nav: NavItem[] = [
    { label: "Dashboard", icon: "grid", active: true },
    { label: "Wallet", icon: "wallet", onClick: () => setDialog("account") },
    { label: "Billing & invoices", icon: "receipt", onClick: () => navigate(paths.customerBilling) },
    { label: "Profile", icon: "user", onClick: () => setDialog("profile") },
  ];

  return (
    <div className="flex min-h-full flex-col bg-background lg:flex-row">
      <AppSidebar roleLabel="Customer" userName={name || "Customer"} userPhone={p ? phone(p) : `ID ${session.userId}`}
        items={nav} onSignOut={clearSession} />

      <main className="min-w-0 flex-1 px-4 py-8 sm:px-6 lg:px-10 lg:py-10">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight">
              Welcome back{p?.first_name ? `, ${p.first_name}` : ""}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">Track your services, documents and wallet in one place.</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button variant="outline" className="h-11 rounded-xl" disabled title={NOT_YET}>
              <Icon name="message" /> Feedback & grievance
            </Button>
            <Button className="h-11 rounded-xl" onClick={() => setDialog("new")} disabled={!state.data}>
              <Icon name="plus" /> Choose new service
            </Button>
          </div>
        </div>

        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl bg-navy p-6 text-navy-foreground">
            <p className="flex items-center gap-2 text-sm font-semibold"><Icon name="wallet" /> Wallet</p>
            <p className="mt-3 text-2xl font-extrabold tabular-nums">{balance}</p>
          </div>
          <StatCard label="Services opted" value={stat(mine.length)} />
          <StatCard label="Awaiting report" value={stat(awaitingReport)} tone="info" />
          <StatCard label="Payment due" value={stat(paymentDue)} tone="warning" />
        </div>

        <section className="mt-8 overflow-hidden rounded-2xl border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-5">
            <h2 className="text-lg font-bold">My services</h2>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Icon name="lock" className="h-3.5 w-3.5" /> Documents are locked once approved
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="border-y bg-muted/60 text-left text-xs font-semibold text-muted-foreground">
                <tr>
                  <th className="px-5 py-4">Service</th>
                  <th className="px-5 py-4">Documents</th>
                  <th className="px-5 py-4">Progress</th>
                  <th className="px-5 py-4">Status</th>
                  <th className="px-5 py-4">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {state.isPending ? (
                  <Message>Loading your services…</Message>
                ) : state.isError ? (
                  <Message>
                    <p>{state.error.message}</p>
                    <Button variant="outline" size="sm" className="mt-3 rounded-lg" onClick={() => state.refetch()}>Try again</Button>
                  </Message>
                ) : mine.length === 0 ? (
                  <Message>
                    <p>You haven't opted into any services yet.</p>
                    <Button size="sm" className="mt-3 rounded-lg" onClick={() => setDialog("new")}>
                      <Icon name="plus" /> Choose new service
                    </Button>
                  </Message>
                ) : (
                  mine.map((s) => <ServiceRow key={s.code} service={s} />)
                )}
              </tbody>
            </table>
          </div>
          {state.data?.payment_in_progress && (
            <p className="border-t bg-warning/40 px-5 py-3 text-sm text-warning-foreground">
              A payment is being confirmed. You can choose new services once it is done.
            </p>
          )}
        </section>
      </main>

      {state.data && (
        <NewServiceDialog open={dialog === "new"} onClose={() => setDialog(null)} services={state.data.services}
          firstPayment={state.data.first_payment} blocked={state.data.payment_in_progress} bdId={p?.broker_id ?? null} />
      )}
      <Dialog open={dialog === "profile"} onClose={() => setDialog(null)} title="Profile">
        {p ? (
          <div className="space-y-4">
            {([["Name", name], ["Mobile", phone(p)], ["Email", p.email_address ?? "Not added"], ["Address", address(p) || "Not added"]] as const).map(([label, value]) => (
              <div key={label}>
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
                <p className="mt-0.5 text-sm font-semibold">{value}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{profile.isError ? profile.error.message : "Loading…"}</p>
        )}
        <p className="text-xs text-muted-foreground">To update these details, contact your Business Dealer.</p>
      </Dialog>
      <Dialog open={dialog === "account"} onClose={() => setDialog(null)} title="Wallet">
        <p className="text-sm text-muted-foreground">Current balance <span className="font-bold text-foreground">{balance}</span></p>
        {wallet.data && wallet.data.locked_amount !== null && wallet.data.locked_amount > 0 && (
          <p className="text-sm text-muted-foreground">Locked {inr(wallet.data.locked_amount)}</p>
        )}
      </Dialog>
    </div>
  );
}

function ServiceRow({ service: s }: { service: CustomerService }) {
  const toast = useToast();
  const upload = useUploadMyDocument();
  const [opening, setOpening] = useState(false);
  const doc = s.document;
  const progress = PROGRESS[statusKey(s.status)] ?? 0;
  const status = serviceStatus(s);
  const canUpload = s.can_upload && (!doc || isRejected(s));

  const onFile = (file: File | undefined) => {
    if (!file) return;
    upload.mutate({ code: s.code, file }, {
      onSuccess: () => toast(`${file.name} uploaded: sent for analysis`, "success"),
    });
  };

  const view = async () => {
    setOpening(true);
    try {
      await downloadMyDocument(s.code);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not open the document", "default");
    } finally {
      setOpening(false);
    }
  };

  return (
    <tr>
      <td className="px-5 py-5 align-top">
        <p className="font-bold">{s.name}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{inrShort(s.fee)}, {payLabel(s)}</p>
      </td>
      <td className="space-y-1.5 px-5 py-5">
        {doc ? (
          <div className="flex items-center gap-2">
            <span className="text-primary"><Icon name="file" /></span>
            <span className="max-w-[180px] truncate">{doc.name}</span>
            <button className="font-medium text-primary hover:underline disabled:opacity-60" disabled={opening} onClick={view}>
              {opening ? "Opening…" : "View"}
            </button>
          </div>
        ) : (
          <span className="text-muted-foreground">
            {s.requested ? (isPayLater(s) ? "Locked until BD guarantee" : "Locked until payment is confirmed") : "None yet"}
          </span>
        )}
      </td>
      <td className="px-5 py-5">
        <div className="flex gap-1.5">
          {STEPS.map((step, i) => (
            <div key={step} className="w-12">
              <div className={cn("h-1 rounded-full", i < progress ? "bg-primary" : "bg-border")} />
              <p className="mt-2 text-[10px] text-muted-foreground">{step}</p>
            </div>
          ))}
        </div>
      </td>
      <td className="px-5 py-5"><StatusPill tone={status.tone}>{status.label}</StatusPill></td>
      <td className="px-5 py-5">
        <div className="flex flex-wrap items-center gap-2">
          {canUpload ? (
            <label className={cn("inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg bg-primary px-3 text-sm font-semibold text-primary-foreground hover:bg-primary/90",
              upload.isPending && "pointer-events-none opacity-60")}>
              <Icon name="upload" /> {upload.isPending ? "Uploading…" : "Upload"}
              <input type="file" accept=".pdf,.jpg,.jpeg,.png" className="sr-only" disabled={upload.isPending}
                onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          ) : s.requested ? (
            <span className="text-xs text-muted-foreground">Waiting for your BD</span>
          ) : (
            <span className="text-muted-foreground">No action needed</span>
          )}
        </div>
        {upload.isError && <p className="mt-1 text-xs text-destructive">{upload.error.message}</p>}
      </td>
    </tr>
  );
}

function NewServiceDialog({ open, onClose, services, firstPayment, blocked, bdId }: {
  open: boolean;
  onClose: () => void;
  services: CustomerService[];
  firstPayment: boolean;
  blocked: boolean;
  bdId: string | null;
}) {
  const toast = useToast();
  const catalog = useCatalog();
  const request = useRequestServices();
  const [chosen, setChosen] = useState<CatalogService | null>(null);
  const [mode, setMode] = useState<PaymentMode | null>(null);
  const [consent, setConsent] = useState(false);

  const reset = () => { setChosen(null); setMode(null); setConsent(false); request.reset(); };
  const close = () => { reset(); onClose(); };
  const taken = (code: string) => services.some((s) => s.code === code && (s.opted || s.requested));
  const joiningFee = firstPayment ? (catalog.data?.joining_fee ?? 0) : 0;
  const total = chosen ? chosen.fee + joiningFee : 0;
  const yourBd = bdId ? `Your BD (ID ${bdId})` : "Your BD";

  const send = (m: PaymentMode) =>
    request.mutate(
      { services: [chosen!.code], mode: m, customer_consent: m === "pay_later" && consent },
      {
        onSuccess: (res) => {
          toast(
            res.awaiting_bd
              ? m === "pay_later"
                ? "Request sent to your BD. Upload opens once they stand guarantor."
                : "Payment sent to your BD to confirm. Upload opens once they do."
              : `Payment received. You can now upload documents for ${chosen!.name}.`,
            "success",
          );
          close();
        },
      },
    );

  return (
    <Dialog open={open} onClose={close} title={chosen ? chosen.name : "Choose new service"} wide>
      {chosen ? (
        <>
          <button onClick={reset} className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground">
            <Icon name="left" /> All services
          </button>
          <div className="rounded-xl border p-4">
            <ServiceInfo s={chosen} />
            {joiningFee > 0 && (
              <div className="mt-3 flex justify-between border-t pt-3 text-sm">
                <span className="text-muted-foreground">One-time joining fee</span>
                <span className="font-semibold tabular-nums">{inr(joiningFee)}</span>
              </div>
            )}
            <div className="mt-3 flex items-center justify-between border-t pt-3">
              <span className="font-bold">Total amount</span>
              <span className="text-xl font-extrabold tabular-nums">{inr(total)}</span>
            </div>
          </div>
          <p className="text-sm font-semibold">How would you like to pay?</p>
          <div className="grid grid-cols-2 gap-3">
            {([["pay_now", "Pay now", "Pay by UPI QR"], ["pay_later", "Pay later", "Your BD stands guarantor"]] as const).map(([m, title, text]) => (
              <button key={m} onClick={() => { setMode(m); setConsent(false); }} aria-pressed={mode === m}
                className={cn("rounded-xl border px-4 py-3 text-left transition-colors",
                  mode === m ? "border-primary bg-accent text-accent-foreground" : "bg-card hover:bg-muted")}>
                <span className="block text-sm font-bold">{title}</span>
                <span className="block text-xs text-muted-foreground">{text}</span>
              </button>
            ))}
          </div>

          {mode === "pay_now" && (
            <div className="space-y-3">
              <div className="flex flex-col items-center gap-2 rounded-2xl border bg-muted/40 p-4">
                <UpiQr text={upiLink(UPI_ID, total, `LAS ${chosen.name}`)} size={180} />
                <p className="text-xl font-extrabold tabular-nums">{inr(total)}</p>
                <p className="text-xs text-muted-foreground">Scan with any UPI app. Pay to {UPI_ID}</p>
                <p className="text-xs text-muted-foreground">Sample QR for now: your BD confirms the payment.</p>
              </div>
              <Button className="h-11 w-full rounded-xl" disabled={request.isPending} onClick={() => send("pay_now")}>
                <Icon name="check" /> {request.isPending ? "Sending…" : "I have completed the payment"}
              </Button>
            </div>
          )}

          {mode === "pay_later" && (
            <div className="space-y-3">
              <label className={cn("flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-3.5", consent && "border-primary bg-accent/50")}>
                <input type="checkbox" className="check mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span className="text-sm">
                  <span className="block font-semibold">Your consent</span>
                  <span className="block text-muted-foreground">I hereby promise to pay these service charges of {inrShort(total)}.</span>
                </span>
              </label>
              <p className="text-xs text-muted-foreground">
                {yourBd} will be asked to stand guarantor. You can upload documents once they confirm.
              </p>
              <Button className="h-11 w-full rounded-xl" disabled={!consent || request.isPending} onClick={() => send("pay_later")}>
                {request.isPending ? "Sending…" : "Send guarantee request to BD"}
              </Button>
            </div>
          )}

          {request.isError && (
            <p role="alert" className="rounded-xl bg-destructive/10 px-3 py-2.5 text-sm font-medium text-destructive">{request.error.message}</p>
          )}
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {blocked ? "A payment is being confirmed. You can choose a new service once it is done." : "Select a service to get started."}
          </p>
          <div className="max-h-[60vh] space-y-2.5 overflow-y-auto pr-1">
            {catalog.isPending && <p className="text-sm text-muted-foreground">Loading services…</p>}
            {catalog.isError && <p className="text-sm text-destructive">{catalog.error.message}</p>}
            {catalog.data?.services.map((s) => (
              <div key={s.code} className="flex items-start justify-between gap-4 rounded-xl border bg-card p-4">
                <div className="min-w-0">
                  <ServiceInfo s={s} />
                  {s.external && <p className="mt-1 text-xs text-info-foreground">Documents and payment are handled on the partner portal.</p>}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-2">
                  <span className="text-sm font-bold tabular-nums">{inrShort(s.fee)}</span>
                  {taken(s.code) ? (
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                      <Icon name="check" className="h-3.5 w-3.5" /> Already opted
                    </span>
                  ) : s.external ? (
                    s.portal_url && (
                      <a href={s.portal_url} target="_blank" rel="noopener noreferrer"
                        className="inline-flex h-9 items-center rounded-lg bg-primary px-3 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                        Continue on portal
                      </a>
                    )
                  ) : (
                    <Button size="sm" className="rounded-lg" disabled={blocked} onClick={() => setChosen(s)}>Opt in</Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </Dialog>
  );
}

function ServiceInfo({ s }: { s: CatalogService }) {
  return (
    <div className="min-w-0">
      <p className="text-sm font-bold">{s.name}</p>
      {s.description && <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{s.description}</p>}
    </div>
  );
}

function Message({ children }: { children: ReactNode }) {
  return (
    <tr>
      <td colSpan={5} className="px-5 py-10 text-center text-muted-foreground">{children}</td>
    </tr>
  );
}

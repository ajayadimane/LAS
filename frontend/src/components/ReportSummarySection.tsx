import { ApiError } from "@/api/client";
import type { ReportAnnexures, ReportSummary } from "@/api/services";
import { Button } from "@/components/ui/Button";
import { Icon, type IconName } from "@/components/ui/Icon";
import { KpiCard } from "@/components/ui/KpiCard";
import { StatusPill, type Tone } from "@/components/ui/StatusPill";
import { cn } from "@/lib/cn";
import { isCustomerPaymentDue } from "@/components/ReportPaymentDue";
import type { ReactNode } from "react";
import { inrShort, moneyToRupees, parseFinancialNumber } from "@/lib/format";

const READINESS_COPY: Record<ReportSummary["readiness_status"], { label: string; tone: Tone }> = {
  READY_FOR_SUBMISSION_REVIEW: { label: "Ready for submission review", tone: "success" },
  CORRECTIONS_REQUIRED: { label: "Corrections required", tone: "warning" },
  EXPERT_REVIEW_REQUIRED: { label: "Needs expert review", tone: "warning" },
  INSUFFICIENT_INFORMATION: { label: "Insufficient information", tone: "neutral" },
};

/** The 403 "not available yet" body: { detail: { detail, status, reasons } }. */
interface LockedBody {
  detail?: { detail?: string; status?: string; reasons?: string[] };
}

/**
 * Section 17's report status card: readiness, Must/Should/Good finding
 * counts, a short financial overview, outstanding corrections, evidence
 * references, and the locked/failed-render/error states. Pure presentational
 * -- the two callers (BD's AnalysisReportCard, customer's
 * CustomerAnalysisReportCard) each fetch their own data with their own
 * (bdId/personId vs. "my own") routes and pass the results in here, so the
 * card itself is written once.
 */
export function ReportSummarySection({
  summary,
  summaryPending,
  summaryError,
  onPreviewPdf,
  annexures,
  annexuresPending,
  annexuresError,
  showAnnexures,
  onToggleAnnexures,
  paymentDue,
}: {
  summary: ReportSummary | undefined;
  summaryPending: boolean;
  summaryError: ApiError | null;
  onPreviewPdf: () => void;
  annexures: ReportAnnexures | undefined;
  annexuresPending: boolean;
  annexuresError: ApiError | null;
  showAnnexures: boolean;
  onToggleAnnexures: () => void;
  /** Shown instead of the generic "not available yet" while a Pay later customer still owes the amount. */
  paymentDue?: ReactNode;
}) {
  const locked = (summaryError?.body as LockedBody | undefined)?.detail;

  if (summaryPending) {
    return <p className="mt-3 text-sm text-muted-foreground">Checking for a report…</p>;
  }

  if (summaryError?.status === 404) {
    return <p className="mt-3 text-sm text-muted-foreground">No analysis has been run for this document yet.</p>;
  }

  if (summaryError?.status === 403 && isCustomerPaymentDue(locked?.reasons) && paymentDue) {
    return <>{paymentDue}</>;
  }

  if (summaryError?.status === 403) {
    return (
      <div className="mt-3">
        <p className="text-sm text-muted-foreground">
          Analysis {locked?.status ? `is ${locked.status.toLowerCase()}` : "is not available yet"} -- this
          updates automatically once it finishes.
        </p>
        {!!locked?.reasons?.length && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {locked.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        )}
      </div>
    );
  }

  if (summaryError) {
    return <p className="mt-3 text-sm text-destructive">{summaryError.message}</p>;
  }

  if (!summary) return null;

  const readiness = READINESS_COPY[summary.readiness_status];
  const fo = summary.financial_overview;
  const averageDscr = parseFinancialNumber(fo.average_dscr?.computed_value);

  return (
    <div className="mt-4 space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={readiness.tone} icon={readiness.tone === "success" ? "checkcircle" : "alert"}>{readiness.label}</StatusPill>
        {summary.critical_findings_count > 0 && (
          <StatusPill tone="danger" icon="alertcircle">{summary.critical_findings_count} critical finding(s)</StatusPill>
        )}
      </div>

      <div>
        <h4 className="font-bold">Correction Summary</h4>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <KpiCard label="Must Fix" value={summary.must_fix_count} tone="warning" icon="alertcircle" toneText caption="Critical corrections" />
          <KpiCard label="Should Fix" value={summary.should_fix_count} tone="info" icon="sliders" toneText caption="Recommended corrections" />
          <KpiCard label="Good to Fix" value={summary.good_to_fix_count} tone="success" icon="bulb" toneText caption="Optional improvements" />
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between gap-2">
          <h4 className="font-bold">Financial Overview</h4>
          <span className="text-xs text-muted-foreground">Extracted DPR figures</span>
        </div>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Overview icon="wallet" label="Project Cost" money={fo.project_cost_total} highlight />
          <Overview icon="banknote" label="Requested Loan" money={fo.requested_loan} highlight />
          <Overview icon="usercheck" label="Promoter Contribution" money={fo.promoter_contribution} />
          <Tile icon="chart" label="Average DSCR" value={averageDscr !== null ? averageDscr.toFixed(2) : "Not computable"} muted={averageDscr === null} />
        </div>
      </div>

      <div>
        <p className="text-xs font-semibold text-muted-foreground">Report Information</p>
        <div className="mt-2 flex flex-wrap gap-2 text-sm">
          <InfoChip icon="clipboard">{summary.outstanding_clarifications_count} outstanding corrections/clarifications.</InfoChip>
          <InfoChip icon="report">{summary.evidence_references_count} evidence references.</InfoChip>
          <InfoChip icon="history">Version {summary.version}.</InfoChip>
        </div>
      </div>

      {showAnnexures && (
        <div className="rounded-xl border bg-muted/30 p-3 text-sm">
          {annexuresPending ? (
            <p className="text-muted-foreground">Loading annexures…</p>
          ) : annexuresError ? (
            <p className="text-destructive">{annexuresError.message}</p>
          ) : annexures ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(annexures, null, 2)}
            </pre>
          ) : null}
        </div>
      )}
      <div className="-mx-5 -mb-5 flex flex-wrap items-center justify-between gap-3 border-t bg-muted/40 px-5 py-4 sm:-mx-7 sm:px-7">
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon name="checkcircle" className="h-3.5 w-3.5 text-primary" /> Assessment ready for review and export.
        </p>
        <div className="flex flex-wrap gap-2.5">
          <Button variant="outline-primary" className="rounded-xl" onClick={onToggleAnnexures}>
            <Icon name="file" /> {showAnnexures ? "Hide Annexures" : "View Annexures"}
          </Button>
          <Button className="rounded-xl" onClick={onPreviewPdf}>
            <Icon name="download" /> View / Download PDF
          </Button>
        </div>
      </div>

    </div>
  );
}

function Tile({ icon, label, value, highlight, muted }: { icon: IconName; label: string; value: string; highlight?: boolean; muted?: boolean }) {
  return (
    <div className={cn("flex items-center gap-4 rounded-2xl border p-4", highlight ? "border-primary/20 bg-accent/60" : "bg-muted/30")}>
      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border bg-card text-accent-foreground">
        <Icon name={icon} className="h-5 w-5" />
      </span>
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={cn("mt-0.5 text-lg font-bold tabular-nums", muted && "text-muted-foreground")}>{value}</p>
      </div>
    </div>
  );
}

function InfoChip({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <span className="flex items-center gap-2 rounded-xl border bg-muted/40 px-3.5 py-2">
      <Icon name={icon} className="h-4 w-4 text-primary" /> {children}
    </span>
  );
}

function Overview({ icon, label, money, highlight }: {
  icon: IconName; label: string; highlight?: boolean;
  money: { amount: number | string; unit: "absolute" | "lakh" | "crore" } | null;
}) {
  const rupees = money ? moneyToRupees(money) : null;
  return <Tile icon={icon} label={label} highlight={highlight} muted={rupees === null}
    value={rupees !== null ? inrShort(rupees) : "Not available"} />;
}

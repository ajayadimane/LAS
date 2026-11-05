import { ApiError } from "@/api/client";
import type { ReportAnnexures, ReportSummary } from "@/api/services";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { StatusPill, type Tone } from "@/components/ui/StatusPill";
import { inrShort, moneyToRupees } from "@/lib/format";

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
}) {
  const locked = (summaryError?.body as LockedBody | undefined)?.detail;

  if (summaryPending) {
    return <p className="mt-3 text-sm text-muted-foreground">Checking for a report…</p>;
  }

  if (summaryError?.status === 404) {
    return <p className="mt-3 text-sm text-muted-foreground">No analysis has been run for this document yet.</p>;
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

  return (
    <div className="mt-3 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={readiness.tone}>{readiness.label}</StatusPill>
        {summary.critical_findings_count > 0 && (
          <span className="text-xs font-semibold text-destructive">
            {summary.critical_findings_count} critical finding(s)
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        <CountTile label="Must fix" value={summary.must_fix_count} tone="warning" />
        <CountTile label="Should fix" value={summary.should_fix_count} tone="info" />
        <CountTile label="Good to fix" value={summary.good_to_fix_count} tone="neutral" />
      </div>

      <div className="rounded-xl border p-3">
        <p className="text-xs font-semibold text-muted-foreground">Financial overview</p>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
          <Overview label="Project cost" money={fo.project_cost_total} />
          <Overview label="Requested loan" money={fo.requested_loan} />
          <Overview label="Promoter contribution" money={fo.promoter_contribution} />
          <div>
            <p className="text-xs text-muted-foreground">Average DSCR</p>
            <p className="font-semibold tabular-nums">
              {fo.average_dscr?.computed_value != null ? fo.average_dscr.computed_value.toFixed(2) : "Not computable"}
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          {summary.outstanding_clarifications_count} outstanding correction(s)/clarification(s)
        </span>
        <span>{summary.evidence_references_count} evidence reference(s)</span>
        <span>Version {summary.version}</span>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" className="rounded-lg" onClick={onPreviewPdf}>
          <Icon name="download" /> View / download PDF
        </Button>
        <Button size="sm" variant="outline" className="rounded-lg" onClick={onToggleAnnexures}>
          <Icon name="file" /> {showAnnexures ? "Hide annexures" : "View annexures"}
        </Button>
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
    </div>
  );
}

function CountTile({ label, value, tone }: { label: string; value: number; tone: Tone }) {
  return (
    <div className="rounded-xl border p-3 text-center">
      <p className="text-xl font-extrabold tabular-nums">{value}</p>
      <StatusPill tone={tone}>{label}</StatusPill>
    </div>
  );
}

function Overview({ label, money }: { label: string; money: { amount: number; unit: "absolute" | "lakh" | "crore" } | null }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="font-semibold tabular-nums">{money ? inrShort(moneyToRupees(money)) : "Not available"}</p>
    </div>
  );
}

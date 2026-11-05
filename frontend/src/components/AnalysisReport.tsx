import { useState } from "react";
import { ApiError } from "@/api/client";
import {
  reportPdfPath,
  useServiceReport,
  useServiceReportAnnexures,
  useServiceReportSummary,
  type AnalysisFinding,
  type CustomerService,
} from "@/api/services";
import { DocumentPreview } from "@/components/DocumentPreview";
import { ReportSummarySection } from "@/components/ReportSummarySection";
import { Icon } from "@/components/ui/Icon";
import { StatusPill, type Tone } from "@/components/ui/StatusPill";

const severityTone: Record<AnalysisFinding["severity"], Tone> = {
  critical: "warning",
  high: "warning",
  medium: "info",
  low: "neutral",
  info: "neutral",
};

/**
 * One opted service's DPR analysis report (BD's customer preview, Payment
 * section). Only shown once a document has been uploaded for that service --
 * before that there is nothing to have analyzed. Polls automatically while
 * analysis is still running. Section 17's status card (readiness,
 * Must/Should/Good counts, financial overview, annexures) lives in
 * ReportSummarySection, shared with the customer's own dashboard.
 */
export function AnalysisReportCard({ bdId, personId, service }: { bdId: string; personId: string; service: CustomerService }) {
  const enabled = !!service.document;
  const query = useServiceReport(bdId, personId, service.code, enabled);
  const summary = useServiceReportSummary(bdId, personId, service.code, enabled);
  const [previewingPdf, setPreviewingPdf] = useState(false);
  const [showAnnexures, setShowAnnexures] = useState(false);
  const annexures = useServiceReportAnnexures(bdId, personId, service.code, enabled && showAnnexures);
  if (!enabled) return null;

  return (
    <div className="rounded-2xl border p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon name="report" className="h-4 w-4 text-muted-foreground" />
          <p className="text-sm font-bold">{service.name}: analysis report</p>
        </div>
        {query.data && (
          <StatusPill tone={query.data.status === "completed" ? "success" : "warning"}>
            {query.data.status === "completed" ? "Completed" : "Incomplete"}
          </StatusPill>
        )}
      </div>

      <DocumentPreview
        path={previewingPdf ? reportPdfPath(bdId, personId, service.code) : null}
        name={`${service.name} analysis report.pdf`}
        onClose={() => setPreviewingPdf(false)}
      />

      <ReportSummarySection
        summary={summary.data}
        summaryPending={summary.isPending}
        summaryError={summary.error instanceof ApiError ? summary.error : null}
        onPreviewPdf={() => setPreviewingPdf(true)}
        annexures={annexures.data}
        annexuresPending={annexures.isPending}
        annexuresError={annexures.error instanceof ApiError ? annexures.error : null}
        showAnnexures={showAnnexures}
        onToggleAnnexures={() => setShowAnnexures((v) => !v)}
      />

      {query.data && query.data.findings.length > 0 && (
        <div className="mt-4 space-y-3 border-t pt-4">
          <p className="text-xs font-semibold text-muted-foreground">Findings</p>
          <ul className="space-y-2">
            {query.data.findings.map((f, i) => (
              <li key={i} className="rounded-xl border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill tone={severityTone[f.severity]}>{f.severity}</StatusPill>
                  <p className="text-sm font-bold">{f.title}</p>
                </div>
                <p className="mt-1.5 text-xs text-muted-foreground">{f.explanation}</p>
                <p className="mt-1.5 text-xs font-medium">{f.suggested_action}</p>
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-muted-foreground">{query.data.disclaimer}</p>
        </div>
      )}
    </div>
  );
}

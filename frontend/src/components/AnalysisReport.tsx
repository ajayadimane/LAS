import { useState } from "react";
import { ApiError } from "@/api/client";
import {
  reportPdfPath,
  useServiceReport,
  useServiceReportAnnexures,
  useServiceReportSummary,
  type CustomerService,
} from "@/api/services";
import { DocumentPreview } from "@/components/DocumentPreview";
import { ReportSummarySection } from "@/components/ReportSummarySection";
import { Icon } from "@/components/ui/Icon";
import { StatusPill } from "@/components/ui/StatusPill";

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
    </div>
  );
}

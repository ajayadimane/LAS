import { useState } from "react";
import {
  myReportPdfPath,
  useMyServiceReport,
  useMyServiceReportAnnexures,
  useMyServiceReportSummary,
} from "@/api/customer";
import { ApiError } from "@/api/client";
import type { CustomerService } from "@/api/services";
import { DocumentPreview } from "@/components/DocumentPreview";
import { ReportSummarySection } from "@/components/ReportSummarySection";
import { Icon } from "@/components/ui/Icon";
import { StatusPill } from "@/components/ui/StatusPill";

/**
 * The customer-facing mirror of AnalysisReportCard (BD's customer preview):
 * one of my own opted services' DPR analysis report, same Section 17 status
 * card (ReportSummarySection), against the customer's own `/customer/...`
 * routes rather than a BD's `/bd/customers/{id}/...` ones.
 */
export function CustomerAnalysisReport({ service }: { service: CustomerService }) {
  const enabled = !!service.document;
  const query = useMyServiceReport(service.code, enabled);
  const summary = useMyServiceReportSummary(service.code, enabled);
  const [previewingPdf, setPreviewingPdf] = useState(false);
  const [showAnnexures, setShowAnnexures] = useState(false);
  const annexures = useMyServiceReportAnnexures(service.code, enabled && showAnnexures);
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
        path={previewingPdf ? myReportPdfPath(service.code) : null}
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

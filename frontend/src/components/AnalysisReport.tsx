import { useState } from "react";
import { ApiError } from "@/api/client";
import {
  reportPdfPath,
  useConfirmCustomerPaid,
  useCustomerDues,
  useServiceReport,
  useServiceReportAnnexures,
  useServiceReportSummary,
  type CustomerService,
} from "@/api/services";
import { DocumentPreview } from "@/components/DocumentPreview";
import { ReportPaymentDue } from "@/components/ReportPaymentDue";
import { ReportSummarySection } from "@/components/ReportSummarySection";
import { Button } from "@/components/ui/Button";
import { UpiQr, upiLink } from "@/components/ui/UpiQr";
import { UPI_ID } from "@/data/catalog";
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
  const summary = useServiceReportSummary(bdId, personId, service.code, enabled);
  // The full report only matters once the summary says it is released: a
  // second parallel call would just repeat the same 403 and vendor lookup.
  const query = useServiceReport(bdId, personId, service.code, enabled && summary.isSuccess);
  const [previewingPdf, setPreviewingPdf] = useState(false);
  const [showAnnexures, setShowAnnexures] = useState(false);
  const annexures = useServiceReportAnnexures(bdId, personId, service.code, enabled && showAnnexures);
  const dues = useCustomerDues(bdId, personId);
  const confirm = useConfirmCustomerPaid(bdId, personId);
  const owed = dues.data?.dues.find((d) => d.code === service.code)?.amount ?? null;
  if (!enabled) return null;

  return (
    <div className="overflow-hidden rounded-2xl border bg-card p-5 shadow-card sm:px-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary/15 bg-accent text-accent-foreground">
            <Icon name="chart" className="h-5 w-5" />
          </span>
          <div>
            <p className="text-lg font-bold tracking-tight">{service.name} Report</p>
            <p className="text-sm text-muted-foreground">Review the project assessment, financial overview and recommended corrections.</p>
          </div>
        </div>
        {query.data && (
          <StatusPill tone={query.data.status === "completed" ? "success" : "warning"} icon={query.data.status === "completed" ? "checkcircle" : undefined}>
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
        paymentDue={
          <ReportPaymentDue amount={owed} checking={summary.isFetching} onCheck={() => void summary.refetch()}>
            {owed !== null && (
              <div className="mb-3 flex flex-wrap items-center gap-4">
                <UpiQr text={upiLink(UPI_ID, owed, `LAS ${service.name} report`)} size={140} />
                <p className="text-xs text-muted-foreground">
                  The customer can scan this with any UPI app (pay to {UPI_ID}); it is the same QR they see on their dashboard.
                </p>
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              You stood guarantor for this payment. Neither you nor the customer can open the report until the
              customer pays you and you confirm it here.
            </p>
            <Button className="mt-2" size="sm" disabled={confirm.isPending} onClick={() => confirm.mutate([service.code])}>
              {confirm.isPending ? "Confirming…" : "Customer has paid"}
            </Button>
            {confirm.isError && <p className="mt-2 text-xs text-destructive">Could not record the payment. Please try again.</p>}
          </ReportPaymentDue>
        }
      />
    </div>
  );
}

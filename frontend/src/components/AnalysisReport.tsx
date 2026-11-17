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

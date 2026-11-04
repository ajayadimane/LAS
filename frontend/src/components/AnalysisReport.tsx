import { useState } from "react";
import { ApiError } from "@/api/client";
import { reportPdfPath, useServiceReport, type AnalysisFinding, type CustomerService } from "@/api/services";
import { Button } from "@/components/ui/Button";
import { DocumentPreview } from "@/components/DocumentPreview";
import { Icon } from "@/components/ui/Icon";
import { StatusPill, type Tone } from "@/components/ui/StatusPill";

const severityTone: Record<AnalysisFinding["severity"], Tone> = {
  critical: "warning",
  high: "warning",
  medium: "info",
  low: "neutral",
  info: "neutral",
};

/** The 403 "not available yet" body: { detail: { detail, status, reasons } } --
 * FastAPI wraps our own dict `detail` payload under its own `detail` key. */
interface LockedBody {
  detail?: { detail?: string; status?: string; reasons?: string[] };
}

/**
 * One opted service's DPR analysis report (BD's customer preview, Payment
 * section). Only shown once a document has been uploaded for that service --
 * before that there is nothing to have analyzed. Polls automatically while
 * analysis is still running.
 */
export function AnalysisReportCard({ bdId, personId, service }: { bdId: string; personId: string; service: CustomerService }) {
  const enabled = !!service.document;
  const query = useServiceReport(bdId, personId, service.code, enabled);
  const [previewingPdf, setPreviewingPdf] = useState(false);
  if (!enabled) return null;

  const err = query.error instanceof ApiError ? query.error : null;
  const locked = (err?.body as LockedBody | undefined)?.detail;

  return (
    <div className="rounded-2xl border p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon name="report" className="h-4 w-4 text-muted-foreground" />
          <p className="text-sm font-bold">{service.name}: analysis report</p>
        </div>
        <div className="flex items-center gap-2">
          {query.data && (
            <StatusPill tone={query.data.status === "completed" ? "success" : "warning"}>
              {query.data.status === "completed" ? "Completed" : "Incomplete"}
            </StatusPill>
          )}
          {query.data && (
            <Button size="sm" variant="outline" className="rounded-lg" onClick={() => setPreviewingPdf(true)}>
              <Icon name="download" /> View / download PDF
            </Button>
          )}
        </div>
      </div>
      {query.data && (
        <DocumentPreview
          path={previewingPdf ? reportPdfPath(bdId, personId, service.code) : null}
          name={`${service.name} analysis report.pdf`}
          onClose={() => setPreviewingPdf(false)}
        />
      )}

      {query.isPending ? (
        <p className="mt-3 text-sm text-muted-foreground">Checking for a report…</p>
      ) : err?.status === 404 ? (
        <p className="mt-3 text-sm text-muted-foreground">No analysis has been run for this document yet.</p>
      ) : err?.status === 403 ? (
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
      ) : err ? (
        <p className="mt-3 text-sm text-destructive">{err.message}</p>
      ) : query.data ? (
        <div className="mt-3 space-y-3">
          {query.data.findings.length === 0 ? (
            <p className="text-sm text-muted-foreground">No findings were raised.</p>
          ) : (
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
          )}
          <p className="text-[11px] text-muted-foreground">{query.data.disclaimer}</p>
        </div>
      ) : null}
    </div>
  );
}

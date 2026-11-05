import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type { VendorAck } from "./bd";

/** Mirrors app/schemas/enrolment.py. */
export interface CatalogService {
  code: string;
  name: string;
  fee: number;
  description: string;
  /** Paid and documented on a partner portal, not here. */
  external: boolean;
  portal_url: string | null;
}

export interface Catalog {
  joining_fee: number;
  services: CatalogService[];
}

export interface FileInfo {
  name: string;
  content_type: string;
  size_bytes: number;
}

/** One in-house service for one customer. Statuses are raw vendor values; null = not set. */
export interface CustomerService {
  code: string;
  name: string;
  fee: number;
  opted: boolean;
  /** The customer asked for it themselves; waiting for the BD to authorize or decline. */
  requested: boolean;
  /** Lifecycle: Awaiting BD, Opted, Docs uploaded, Approved, Rejected, Report ready. */
  status: string | null;
  payment_status: string | null;
  payment_mode: string | null;
  /** The pool transfer, for pay later. */
  transaction_id: string | null;
  document: FileInfo | null;
  /** Paid and not yet approved. */
  can_upload: boolean;
}

export interface CustomerServices {
  person_id: string;
  services: CustomerService[];
  /** Customer-level summary of the services. */
  payment_status: string | null;
  payment_mode: string | null;
  doc_status: string | null;
  /** Nothing paid yet: the next payment includes the joining fee. */
  first_payment: boolean;
  /** A pay-later transfer's outcome is unknown. No new payment until support reconciles it. */
  payment_in_progress: boolean;
}

export type PaymentMode = "pay_now" | "pay_later";

export interface PaymentInput {
  services: string[];
  mode: PaymentMode;
  bd_consent?: boolean;
  customer_consent?: boolean;
}

export interface PaymentResponse {
  person_id: string;
  mode: PaymentMode;
  services: string[];
  amount: number;
  /** The part of amount that is the joining fee (first payment only). */
  joining_fee: number;
  payment_status: string;
  transfer: VendorAck | null;
  /** False: the money moved but the customer is not marked paid. Never pay again. */
  status_recorded: boolean;
}

export interface DocumentUploadResponse {
  person_id: string;
  code: string;
  document: FileInfo;
  doc_status: string;
}

/** Response from .../document-with-analysis: stores the document and queues
 * its DPR analysis in one call (app/services/dpr_agentic/document_queue.py). */
export interface DocumentWithAnalysisResponse {
  wallet_storage_status: "STORED" | "FAILED";
  wallet_error: string | null;
  analysis_status: "QUEUED" | "NOT_QUEUED" | "RATE_LIMITED";
  report_status: "NOT_PUBLISHED";
  run_id: string | null;
}

/** Mirrors app/services/dpr_agentic/schemas.py's Finding. */
export interface AnalysisFinding {
  category: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  title: string;
  explanation: string;
  evidence_page: number | null;
  evidence_quote: string | null;
  verification: "source_verified" | "unverified" | "needs_clarification";
  suggested_action: string;
  provenance: "extracted" | "calculated" | "ai_interpreted" | "assumption" | "unverified";
}

/** Mirrors app/services/dpr_agentic/schemas.py's AnalysisResult. */
export interface AnalysisReport {
  service_request_id: string;
  wallet_document_id: string;
  document_sha256: string;
  status: "completed" | "incomplete";
  findings: AnalysisFinding[];
  unresolved_questions: string[];
  disclaimer: string;
}

/** Mirrors app/services/dpr_agentic/cam_snapshot.py's Money. */
export interface Money {
  amount: number;
  currency: string;
  unit: "absolute" | "lakh" | "crore";
}

/** Mirrors app/services/dpr_agentic/cam_snapshot.py's CalculationTrace. */
export interface CalculationTrace {
  formula: string;
  inputs: Record<string, string>;
  computed_value: number | null;
  status: "COMPUTED" | "NOT_COMPUTABLE";
  reason_not_computable: string | null;
  calculation_version: string;
}

export type ReadinessStatus =
  | "READY_FOR_SUBMISSION_REVIEW"
  | "CORRECTIONS_REQUIRED"
  | "EXPERT_REVIEW_REQUIRED"
  | "INSUFFICIENT_INFORMATION";

/** Section 17 dashboard status card -- mirrors report_http.resolve_summary(). */
export interface ReportSummary {
  version: number;
  readiness_status: ReadinessStatus;
  must_fix_count: number;
  should_fix_count: number;
  good_to_fix_count: number;
  critical_findings_count: number;
  outstanding_clarifications_count: number;
  evidence_references_count: number;
  financial_overview: {
    project_cost_total: Money | null;
    requested_loan: Money | null;
    promoter_contribution: Money | null;
    funding_gap: CalculationTrace | null;
    average_dscr: CalculationTrace | null;
  };
}

/** Mirrors report_http.resolve_annexures()'s dict shape: Section 12's
 * seven annexures (A-G), each with its own shape plus a `missing` list --
 * see app/services/dpr_agentic/cam_annexures.py. Left loose here since the
 * frontend only shows this as a read-only JSON panel today. */
export interface ReportAnnexureSection {
  missing: string[];
  [key: string]: unknown;
}

export interface ReportAnnexures {
  A_financial_statements: ReportAnnexureSection;
  B_repayment: ReportAnnexureSection;
  C_sensitivity_analysis: ReportAnnexureSection;
  D_project_feasibility: ReportAnnexureSection;
  E_findings_register: ReportAnnexureSection;
  F_sbi_and_scheme_references: ReportAnnexureSection;
  G_audit_trail: ReportAnnexureSection;
}

/** The service catalogue and fees, from the middleware's config. */
export const useCatalog = () =>
  useQuery({
    queryKey: ["catalog"],
    queryFn: () => api.get<Catalog>("services"),
    staleTime: 10 * 60_000,
  });

const customerPath = (bdId: string, personId: string, rest: string) =>
  `bd/customers/${encodeURIComponent(personId)}/${rest}?broker_id=${encodeURIComponent(bdId)}`;

const servicesKey = (bdId: string, personId: string) => ["bd", bdId, "customers", personId, "services"];

/** A customer's opted services, payment and document state. */
export const useCustomerServices = (bdId: string, personId: string | null) =>
  useQuery({
    queryKey: servicesKey(bdId, personId ?? ""),
    queryFn: () => api.get<CustomerServices>(customerPath(bdId, personId!, "services")),
    enabled: personId !== null,
  });

/**
 * Opt in and pay. Never retried: Pay later moves money. A 502 with code
 * PAYMENT_OUTCOME_UNKNOWN means the transfer may have happened.
 */
export const usePayment = (bdId: string, personId: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: PaymentInput) => api.post<PaymentResponse>(customerPath(bdId, personId, "payment"), input),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: servicesKey(bdId, personId) });
      queryClient.invalidateQueries({ queryKey: ["bd", bdId, "customers"] });
    },
  });
};

export const isOutcomeUnknown = (e: unknown) => e instanceof ApiError && e.code === "PAYMENT_OUTCOME_UNKNOWN";

/** Where one service's document is served (for fetchFile). */
export const documentPath = (bdId: string, personId: string, code: string) =>
  customerPath(bdId, personId, `services/${encodeURIComponent(code)}/document`);

/** Where one service's DPR analysis report is served as a PDF (for fetchFile) --
 * the same already-gated report useServiceReport reads as JSON, pre-rendered
 * server-side (see app/services/dpr_agentic/report_store.py), never regenerated
 * on this request. */
export const reportPdfPath = (bdId: string, personId: string, code: string) =>
  `${customerPath(bdId, personId, `services/${encodeURIComponent(code)}/report`)}&format=pdf`;

/** Where one service's Annexure B figures are served (project cost, means
 * of finance, financial ratios, scenario analysis) -- the same gated
 * report as useServiceReport, reshaped, never a separate render. */
export const reportAnnexuresPath = (bdId: string, personId: string, code: string) =>
  customerPath(bdId, personId, `services/${encodeURIComponent(code)}/report/annexures`);

const reportKey = (bdId: string, personId: string, code: string) =>
  ["bd", bdId, "customers", personId, "services", code, "report"];

const reportSummaryKey = (bdId: string, personId: string, code: string) =>
  ["bd", bdId, "customers", personId, "services", code, "report-summary"];

/**
 * Uploads one service's document and queues its DPR analysis in the same
 * call (it replaces any earlier document). Analysis itself runs later,
 * out of band -- see useServiceReport for polling its result.
 */
export const useUploadDocument = (bdId: string, personId: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ code, file }: { code: string; file: File }) =>
      api.put<DocumentWithAnalysisResponse>(
        `${customerPath(bdId, personId, `services/${encodeURIComponent(code)}/document-with-analysis`)}` +
          `&filename=${encodeURIComponent(file.name)}`,
        file,
      ),
    onSuccess: (_res, { code }) => {
      queryClient.invalidateQueries({ queryKey: servicesKey(bdId, personId) });
      queryClient.invalidateQueries({ queryKey: ["bd", bdId, "customers"] });
      queryClient.invalidateQueries({ queryKey: reportKey(bdId, personId, code) });
    },
  });
};

/**
 * One service's DPR analysis report, once it is ready. A completed report
 * comes back as normal query data; "not available yet" (403, still running),
 * "never run" (404) and "feature off" (404 before any run exists) all come
 * back as an ApiError on query.error -- the component reads error.status.
 * Polls every 10s while the report might still be on its way.
 */
export const useServiceReport = (bdId: string, personId: string, code: string, enabled: boolean) =>
  useQuery<AnalysisReport, ApiError>({
    queryKey: reportKey(bdId, personId, code),
    queryFn: () => api.get<AnalysisReport>(customerPath(bdId, personId, `services/${encodeURIComponent(code)}/report`)),
    enabled,
    retry: false,
    refetchInterval: (query) => (query.state.data ? false : query.state.error?.status === 403 ? 10_000 : false),
  });

/** Section 17 status card for one service's report: readiness, Must/Should/
 * Good counts, financial overview. Same gate and polling behaviour as
 * useServiceReport, served by the same backend call -- just a smaller body. */
export const useServiceReportSummary = (bdId: string, personId: string, code: string, enabled: boolean) =>
  useQuery<ReportSummary, ApiError>({
    queryKey: reportSummaryKey(bdId, personId, code),
    queryFn: () => api.get<ReportSummary>(customerPath(bdId, personId, `services/${encodeURIComponent(code)}/report/summary`)),
    enabled,
    retry: false,
    refetchInterval: (query) => (query.state.data ? false : query.state.error?.status === 403 ? 10_000 : false),
  });

/** Annexure B figures, fetched only once the user opens that panel. */
export const useServiceReportAnnexures = (bdId: string, personId: string, code: string, enabled: boolean) =>
  useQuery<ReportAnnexures, ApiError>({
    queryKey: [...reportKey(bdId, personId, code), "annexures"],
    queryFn: () => api.get<ReportAnnexures>(reportAnnexuresPath(bdId, personId, code)),
    enabled,
    retry: false,
  });

/** BD: authorize a customer's own request. Pay later needs bd_consent (standing guarantor) and moves money. */
export const useAuthorize = (bdId: string, personId: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ code, bdConsent }: { code: string; bdConsent: boolean }) =>
      api.post<PaymentResponse>(
        customerPath(bdId, personId, `services/${encodeURIComponent(code)}/authorize`),
        { bd_consent: bdConsent },
      ),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: servicesKey(bdId, personId) });
      queryClient.invalidateQueries({ queryKey: ["bd", bdId, "customers"] });
    },
  });
};

/** BD: decline a customer's own request. Nothing is charged. */
export const useDecline = (bdId: string, personId: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (code: string) =>
      api.post<CustomerServices>(customerPath(bdId, personId, `services/${encodeURIComponent(code)}/decline`)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: servicesKey(bdId, personId) });
      queryClient.invalidateQueries({ queryKey: ["bd", bdId, "customers"] });
    },
  });
};

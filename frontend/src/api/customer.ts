import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, downloadFile } from "./client";
import type { PostalAddress } from "./bd";
import type {
  AnalysisReport,
  CustomerServices,
  DocumentUploadResponse,
  PaymentMode,
  PaymentResponse,
  ReportAnnexures,
  ReportSummary,
} from "./services";
import type { CustomerServices, DocumentUploadResponse, PaymentMode, PaymentResponse, ServiceDetails } from "./services";

/** The signed-in customer's own record. Mirrors app/api/v1/customer.py. */

export interface ServiceRequestInput {
  services: string[];
  mode: PaymentMode;
  /** Pay later: "I promise to pay these service charges". */
  customer_consent?: boolean;
  /** Required for every chosen service with details. */
  details?: ServiceDetails;
}

export interface ServiceRequestResponse {
  person_id: string;
  mode: PaymentMode;
  services: string[];
  amount: number;
  /** True: nothing is charged until the BD authorizes. */
  awaiting_bd: boolean;
  payment: PaymentResponse | null;
}

/** Mirrors app/schemas/customer.py. */
export interface CustomerProfile {
  person_id: string;
  first_name: string | null;
  last_name: string | null;
  mobile_number: string | null;
  mobile_country_code: string | null;
  email_address: string | null;
  address: PostalAddress;
  /** The id of the customer's BD. */
  broker_id: string | null;
}

export const useMyProfile = () =>
  useQuery({ queryKey: ["customer", "profile"], queryFn: () => api.get<CustomerProfile>("customer/profile"), staleTime: 5 * 60_000 });

export const downloadMyDocument = (code: string) =>
  downloadFile(`customer/services/${encodeURIComponent(code)}/document`, `${code.toLowerCase()}-document`);

const myServicesKey = ["customer", "services"];

export const useMyServices = () =>
  useQuery({ queryKey: myServicesKey, queryFn: () => api.get<CustomerServices>("customer/services") });

/** Opt into services. Pay later (and, while the QR is a placeholder, pay now) waits for the BD. */
export const useRequestServices = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ServiceRequestInput) => api.post<ServiceRequestResponse>("customer/services/request", input),
    onSettled: () => queryClient.invalidateQueries({ queryKey: myServicesKey }),
  });
};

export const useUploadMyDocument = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ code, file }: { code: string; file: File }) =>
      api.put<DocumentUploadResponse>(
        `customer/services/${encodeURIComponent(code)}/document?filename=${encodeURIComponent(file.name)}`,
        file,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: myServicesKey }),
  });
};

/** Where my own service's DPR analysis report is served as a PDF (for DocumentPreview). */
export const myReportPdfPath = (code: string) =>
  `customer/services/${encodeURIComponent(code)}/report?format=pdf`;

export const myReportAnnexuresPath = (code: string) =>
  `customer/services/${encodeURIComponent(code)}/report/annexures`;

const myReportKey = (code: string) => ["customer", "services", code, "report"];
const myReportSummaryKey = (code: string) => ["customer", "services", code, "report-summary"];

/** My own service's DPR analysis report, once it may be released. See
 * services.ts's useServiceReport (the BD equivalent) for the polling and
 * error-shape contract -- identical here, just against the customer's own
 * routes (no bdId/personId). */
export const useMyServiceReport = (code: string, enabled: boolean) =>
  useQuery<AnalysisReport, ApiError>({
    queryKey: myReportKey(code),
    queryFn: () => api.get<AnalysisReport>(`customer/services/${encodeURIComponent(code)}/report`),
    enabled,
    retry: false,
    refetchInterval: (query) => (query.state.data ? false : query.state.error?.status === 403 ? 10_000 : false),
  });

/** Section 17 status card for my own service's report. */
export const useMyServiceReportSummary = (code: string, enabled: boolean) =>
  useQuery<ReportSummary, ApiError>({
    queryKey: myReportSummaryKey(code),
    queryFn: () => api.get<ReportSummary>(`customer/services/${encodeURIComponent(code)}/report/summary`),
    enabled,
    retry: false,
    refetchInterval: (query) => (query.state.data ? false : query.state.error?.status === 403 ? 10_000 : false),
  });

/** My own service's Annexure B figures, fetched only once that panel opens. */
export const useMyServiceReportAnnexures = (code: string, enabled: boolean) =>
  useQuery<ReportAnnexures, ApiError>({
    queryKey: [...myReportKey(code), "annexures"],
    queryFn: () => api.get<ReportAnnexures>(myReportAnnexuresPath(code)),
    enabled,
    retry: false,
  });

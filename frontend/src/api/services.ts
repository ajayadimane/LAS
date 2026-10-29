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

/** Uploads one service's document; it replaces any earlier one. */
export const useUploadDocument = (bdId: string, personId: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ code, file }: { code: string; file: File }) =>
      api.put<DocumentUploadResponse>(
        `${documentPath(bdId, personId, code)}&filename=${encodeURIComponent(file.name)}`,
        file,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: servicesKey(bdId, personId) });
      queryClient.invalidateQueries({ queryKey: ["bd", bdId, "customers"] });
    },
  });
};

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

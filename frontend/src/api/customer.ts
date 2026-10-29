import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadFile } from "./client";
import type { PostalAddress } from "./bd";
import type { CustomerServices, DocumentUploadResponse, PaymentMode, PaymentResponse } from "./services";

/** The signed-in customer's own record. Mirrors app/api/v1/customer.py. */

export interface ServiceRequestInput {
  services: string[];
  mode: PaymentMode;
  /** Pay later: "I promise to pay these service charges". */
  customer_consent?: boolean;
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

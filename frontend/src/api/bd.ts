import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";

/** Mirrors app/schemas/bd.py. */
export interface PostalAddress {
  street_address: string | null;
  city: string | null;
  state_province: string | null;
  postal_code: string | null;
  country_code: string | null;
}

export interface OptedService {
  code: string;
  name: string;
  fee: number;
}

export interface BdCustomer {
  person_id: string | null;
  first_name: string | null;
  last_name: string | null;
  mobile_number: string | null;
  mobile_country_code: string | null;
  email_address: string | null;
  address: PostalAddress;
  status: string | null;
  created_at: string | null;
  services: OptedService[];
  /** Services the customer requested themselves, waiting for this BD. */
  requests: OptedService[];
  /** Raw vendor values; null means "NA" (not set). See lib/customerStatus.ts. */
  doc_status: string | null;
  payment_status: string | null;
  payment_mode: string | null;
}

export interface BdCustomerList {
  broker_id: string;
  count: number;
  customers: BdCustomer[];
}

/** The customers registered under this BD (the bdId saved at login). */
export const useBdCustomers = (bdId: string) =>
  useQuery({
    queryKey: ["bd", bdId, "customers"],
    queryFn: () => api.get<BdCustomerList>(`bd/customers?broker_id=${encodeURIComponent(bdId)}`),
  });

/**
 * One of this BD's customers, fresh from the vendor. Shows the dashboard's
 * cached list entry meanwhile, so opening a customer renders instantly.
 * 404 when the person is not registered under this BD.
 */
export const useBdCustomer = (bdId: string, personId: string) => {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["bd", bdId, "customers", personId],
    queryFn: () =>
      api.get<BdCustomer>(`bd/customers/${encodeURIComponent(personId)}?broker_id=${encodeURIComponent(bdId)}`),
    placeholderData: () =>
      queryClient
        .getQueryData<BdCustomerList>(["bd", bdId, "customers"])
        ?.customers.find((c) => c.person_id === personId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
};

/** POST /api/v1/bd/customers body. Mirrors CreateCustomerRequest. */
export interface CreateCustomerInput {
  first_name: string;
  last_name?: string;
  mobile_number: string;
  email_address: string;
  street_address: string;
  city: string;
  state_province: string;
  postal_code: string;
  country_code?: string;
}

export interface VendorAck {
  transaction_id: string | null;
  correlation_id: string | null;
  timestamp: string | null;
}

export interface StepError {
  status: number;
  code: string | null;
  message: string;
  transaction_id: string | null;
  correlation_id: string | null;
}

export interface CreateCustomerResponse {
  person_id: string;
  registration: VendorAck;
  /** False means the customer IS registered but has no wallet yet: retry with useOpenWallet. */
  wallet_opened: boolean;
  wallet: VendorAck | null;
  wallet_error: StepError | null;
  /** False means the customer IS registered but has no login yet: retry with useCreateLogin. */
  login_created: boolean;
  login_user_id: string;
  login: VendorAck | null;
  login_error: StepError | null;
  /** Shown to the BD once and never stored: it exists nowhere else. */
  temporary_password: string | null;
  /** The login details were emailed to the customer. */
  credentials_emailed: boolean;
  /** Why not, if they were not. */
  credentials_email_error: string | null;
}

export interface CreateLoginResponse {
  person_id: string;
  login_user_id: string;
  /** The login was already there; its password is unchanged and not returned. */
  already_exists: boolean;
  login: VendorAck | null;
  temporary_password: string | null;
  /** The login details were emailed to the customer. */
  credentials_emailed: boolean;
  /** Why not, if they were not. */
  credentials_email_error: string | null;
}

export interface OpenWalletResponse {
  person_id: string;
  already_open: boolean;
  wallet: VendorAck | null;
}

const customersKey = (bdId: string) => ["bd", bdId, "customers"];

/** Registers a customer under this BD, opens their wallet and creates their login. Never retried: it is a write. */
export const useCreateCustomer = (bdId: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateCustomerInput) =>
      api.post<CreateCustomerResponse>(`bd/customers?broker_id=${encodeURIComponent(bdId)}`, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: customersKey(bdId) }),
  });
};

/** Opens (or confirms) the wallet of one of this BD's customers. Safe to repeat. */
export const useOpenWallet = (bdId: string) =>
  useMutation({
    mutationFn: (personId: string) =>
      api.post<OpenWalletResponse>(
        `bd/customers/${encodeURIComponent(personId)}/wallet?broker_id=${encodeURIComponent(bdId)}`,
      ),
  });

/** Creates (or confirms) the login of one of this BD's customers. Safe to repeat. */
export const useCreateLogin = (bdId: string) =>
  useMutation({
    mutationFn: (personId: string) =>
      api.post<CreateLoginResponse>(
        `bd/customers/${encodeURIComponent(personId)}/login?broker_id=${encodeURIComponent(bdId)}`,
      ),
  });

/** PUT /email and /address. Mirrors UpdateCustomerResponse. */
export interface UpdateCustomerResponse {
  person_id: string;
  updated: VendorAck;
}

export type AddressInput = Required<{ [K in keyof PostalAddress]: string }>;

const customerPath = (bdId: string, personId: string, rest: string) =>
  `bd/customers/${encodeURIComponent(personId)}/${rest}?broker_id=${encodeURIComponent(bdId)}`;

/** Each detail is its own vendor call, so each is its own mutation. Never retried: they are writes. */
function useUpdateDetail<T>(bdId: string, personId: string, rest: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: T) => api.put<UpdateCustomerResponse>(customerPath(bdId, personId, rest), input),
    // The list key is a prefix of the customer's own key, so both refresh.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: customersKey(bdId) }),
  });
}

export const useUpdateEmail = (bdId: string, personId: string) =>
  useUpdateDetail<{ email_address: string }>(bdId, personId, "email");

export const useUpdateAddress = (bdId: string, personId: string) =>
  useUpdateDetail<AddressInput>(bdId, personId, "address");

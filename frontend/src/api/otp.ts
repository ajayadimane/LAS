import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "./client";

/** Mirrors app/schemas/otp.py. */
export type OtpChannel = "mobile" | "email";
/** register: a BD adding a customer (personId = the mobile). update: editing an existing customer. */
export type OtpPurpose = "register" | "update";

export interface OtpSettings {
  /** False only in local runs: then saves need no OTP and the UI hides it. */
  required: boolean;
  code_length: number;
  max_attempts: number;
  resend_after_seconds: number;
}

export interface SendOtpInput {
  channel: OtpChannel;
  value: string;
  purpose: OtpPurpose;
  person_id: string;
}

export interface SendOtpResponse {
  /** Opaque; goes back with the code. */
  otp_session: string;
  sent_to: string;
  expires_at: number;
  resend_after_seconds: number;
  max_attempts: number;
  code_length: number;
}

export interface VerifyOtpResponse {
  verified: true;
  channel: OtpChannel;
  value: string;
  /** Sent with the save, as mobile_otp_token / email_otp_token. */
  verification_token: string;
  expires_at: number;
}

export const useOtpSettings = () =>
  useQuery({
    queryKey: ["otp", "settings"],
    queryFn: () => api.get<OtpSettings>("otp/settings"),
    staleTime: Infinity,
  });

/** Sends an OTP. Never retried: a retry would send a second one. */
export const useSendOtp = () =>
  useMutation({ mutationFn: (input: SendOtpInput) => api.post<SendOtpResponse>("otp/send", input) });

/** Checks a code. Never retried: each try spends an attempt. */
export const useVerifyOtp = () =>
  useMutation({
    mutationFn: (input: { otp_session: string; otp: string }) => api.post<VerifyOtpResponse>("otp/verify", input),
  });

/** The form the middleware compares: 10 digits, or a lowercased email. */
export const normaliseOtpValue = (channel: OtpChannel, value: string) =>
  channel === "mobile" ? value.replace(/\D/g, "") : value.trim().toLowerCase();

/** A verified value, kept by the form until the save. */
export interface OtpProof {
  value: string;
  personId: string;
  token: string;
}

/** True when `proof` was issued for exactly this value and customer. */
export const proves = (proof: OtpProof | undefined, channel: OtpChannel, value: string, personId: string) =>
  !!proof && proof.value === normaliseOtpValue(channel, value) && proof.personId === personId;

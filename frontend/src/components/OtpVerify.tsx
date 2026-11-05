import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import {
  normaliseOtpValue, useSendOtp, useVerifyOtp,
  type OtpChannel, type OtpProof, type OtpPurpose, type SendOtpResponse,
} from "@/api/otp";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";

/**
 * Verify a mobile number or email with an OTP: send, type the code, verify.
 * The proof goes to the parent, which sends it with the save. Changing the
 * value (or the customer) after sending starts over: the session is bound to
 * exactly what was sent.
 */
export function OtpVerify({
  channel, value, personId, purpose, ready, verified, onVerified, onError, disabled,
}: {
  channel: OtpChannel;
  value: string;
  personId: string;
  purpose: OtpPurpose;
  /** The value is well-formed (and the customer known), so an OTP can be sent. */
  ready: boolean;
  /** The parent holds a proof for this exact value. */
  verified: boolean;
  onVerified: (proof: OtpProof) => void;
  /** Lets the parent react to a refusal, e.g. CUSTOMER_ALREADY_REGISTERED. */
  onError?: (error: ApiError) => void;
  disabled?: boolean;
}) {
  const send = useSendOtp();
  const check = useVerifyOtp();
  const [session, setSession] = useState<(SendOtpResponse & { for: string }) | null>(null);
  const [code, setCode] = useState("");
  const [wait, setWait] = useState(0);
  const target = `${personId}|${normaliseOtpValue(channel, value)}`;
  const label = channel === "mobile" ? "mobile number" : "email";

  // A different value or customer: the old session proves nothing about it.
  useEffect(() => {
    if (session && session.for !== target) {
      setSession(null);
      setCode("");
      setWait(0); // the cooldown is per value
      check.reset();
    }
    send.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  useEffect(() => {
    if (wait <= 0) return;
    const t = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);

  if (verified) {
    return (
      <p className="mt-1.5 flex items-center gap-1.5 text-xs font-semibold text-success-foreground">
        <Icon name="check" className="h-3.5 w-3.5" /> {channel === "mobile" ? "Mobile number" : "Email"} verified
      </p>
    );
  }

  const doSend = () =>
    send.mutate(
      { channel, value: normaliseOtpValue(channel, value), purpose, person_id: personId },
      {
        onSuccess: (res) => {
          setSession({ ...res, for: target });
          setCode("");
          check.reset();
          setWait(res.resend_after_seconds);
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            onError?.(err);
            // Too soon: show the countdown on the button instead.
            const secs = err.code === "OTP_RESEND_TOO_SOON" && /(\d+) seconds/.exec(err.message)?.[1];
            if (secs) setWait(Number(secs));
          }
        },
      },
    );

  const exhausted = check.error instanceof ApiError
    && ["OTP_ATTEMPTS_EXCEEDED", "OTP_SESSION_EXPIRED", "OTP_ALREADY_USED"].includes(check.error.code ?? "");

  const doVerify = () => {
    if (!session) return;
    check.mutate(
      { otp_session: session.otp_session, otp: code },
      { onSuccess: (res) => onVerified({ value: res.value, personId, token: res.verification_token }) },
    );
  };

  const length = session?.code_length ?? 6;
  const sendLabel = send.isPending ? "Sending…" : wait > 0 ? `Resend in ${wait}s` : session ? "Resend OTP" : "Send OTP";

  return (
    <div className="mt-2 space-y-2">
      {session && !exhausted && (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            inputMode="numeric" autoComplete="one-time-code" maxLength={length}
            aria-label={`OTP sent to the ${label}`} placeholder={"•".repeat(length)}
            className="otp h-10 w-40 text-center font-bold" disabled={disabled || check.isPending}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, length))}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault(); // not the form's submit
                if (code.length === length) doVerify();
              }
            }}
          />
          <Button size="sm" className="rounded-lg" onClick={doVerify}
            disabled={disabled || code.length !== length || check.isPending}>
            {check.isPending ? "Verifying…" : "Verify"}
          </Button>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Button size="sm" variant="outline" className="rounded-lg" onClick={doSend}
          disabled={disabled || !ready || send.isPending || wait > 0}>
          {sendLabel}
        </Button>
        {session && !exhausted && !check.isError && (
          <p className="text-xs text-muted-foreground">Code sent to {session.sent_to}.</p>
        )}
        {!session && !send.isError && (
          <p className="text-xs text-muted-foreground">Verify the {label} with a one-time code.</p>
        )}
      </div>
      {send.isError && <p className="text-xs text-destructive">{send.error.message}</p>}
      {check.isError && (
        <p className="text-xs text-destructive">{check.error.message}</p>
      )}
    </div>
  );
}

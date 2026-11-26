import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import {
  normaliseOtpValue, useSendOtp, useVerifyOtp,
  type OtpChannel, type OtpProof, type OtpPurpose, type SendOtpResponse,
} from "@/api/otp";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

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
      <p className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-success px-2.5 py-1 text-xs font-semibold text-success-foreground">
        <Icon name="checkcircle" className="h-3.5 w-3.5" /> {channel === "mobile" ? "Mobile number" : "Email"} verified
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

  const mmss = `${String(Math.floor(wait / 60)).padStart(2, "0")}:${String(wait % 60).padStart(2, "0")}`;

  return (
    <div className="mt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {!(session && !exhausted) && (
          <Button size="sm" variant="outline" className="h-8 rounded-lg border-primary/30 bg-accent text-xs text-accent-foreground hover:bg-accent/70"
            onClick={doSend} disabled={disabled || !ready || send.isPending || wait > 0}>
            <Icon name="checkcircle" className="h-3.5 w-3.5" /> {sendLabel}
          </Button>
        )}
        {!session && !send.isError && (
          <p className="text-xs text-muted-foreground">Verify the {label} with a one-time code.</p>
        )}
      </div>
      {session && !exhausted && (
        <div className="rounded-2xl border border-primary/15 bg-surface p-4">
          <p className="text-xs text-muted-foreground">
            Enter the {length}-digit OTP sent to <span className="font-semibold text-accent-foreground">{session.sent_to}</span>
          </p>
          <OtpBoxes length={length} value={code} label={`OTP sent to the ${label}`} disabled={disabled || check.isPending}
            onChange={setCode} onEnter={() => code.length === length && doVerify()} />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <Button size="sm" className="rounded-lg" onClick={doVerify}
              disabled={disabled || code.length !== length || check.isPending}>
              {check.isPending ? "Verifying…" : "Verify OTP"}
            </Button>
            {wait > 0 ? (
              <span className="text-xs text-muted-foreground">Resend OTP in {mmss}</span>
            ) : (
              <button type="button" className="text-xs font-semibold text-primary hover:underline disabled:opacity-50"
                onClick={doSend} disabled={disabled || !ready || send.isPending}>
                {send.isPending ? "Sending…" : "Resend OTP"}
              </button>
            )}
          </div>
        </div>
      )}
      {send.isError && <p className="text-xs text-destructive">{send.error.message}</p>}
      {check.isError && (
        <p className="text-xs text-destructive">{check.error.message}</p>
      )}
    </div>
  );
}

/** One real input (paste, autofill, screen readers) drawn as a row of boxes. */
function OtpBoxes({ length, value, label, disabled, onChange, onEnter }: {
  length: number; value: string; label: string; disabled?: boolean;
  onChange: (v: string) => void; onEnter: () => void;
}) {
  const [focused, setFocused] = useState(false);
  return (
    <div className="relative mt-3 grid max-w-xs gap-2" style={{ gridTemplateColumns: `repeat(${length}, minmax(0, 1fr))` }}>
      {Array.from({ length }, (_, i) => (
        <span key={i} aria-hidden="true"
          className={cn("flex h-11 items-center justify-center rounded-lg border bg-card text-lg font-bold",
            focused && i === Math.min(value.length, length - 1) ? "border-primary ring-2 ring-primary/20" : "border-input")}>
          {value[i] ?? ""}
        </span>
      ))}
      <input
        inputMode="numeric" autoComplete="one-time-code" maxLength={length} aria-label={label}
        className="absolute inset-0 h-full w-full cursor-text opacity-0" disabled={disabled}
        value={value}
        onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, length))}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault(); // not the form's submit
            onEnter();
          }
        }}
      />
    </div>
  );
}

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Logo } from "@/components/ui/Logo";
import { useToast } from "@/components/ui/Toast";
import { paths } from "@/routes";

// TODO(api): each step should call the middleware (send code, verify code, set password).
export default function ResetPassword() {
  const navigate = useNavigate();
  const toast = useToast();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [identifier, setIdentifier] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");

  return (
    <div className="flex min-h-full justify-center bg-background px-4 py-12">
      <div className="w-full max-w-sm">
        <Logo />
        <p className="mt-8 text-xs font-semibold text-muted-foreground">Step {step} of 3</p>
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight">Reset password</h1>

        {step === 1 && (
          <>
            <p className="mt-2 text-sm text-muted-foreground">
              Enter the email or mobile number on your account. We'll send you a 6-digit code.
            </p>
            <form onSubmit={(e) => { e.preventDefault(); setStep(2); }} className="mt-6 space-y-4">
              <div className="space-y-1.5">
                <label className="text-sm font-semibold" htmlFor="rp-id">Email or mobile number</label>
                <Input id="rp-id" className="h-11" placeholder="name@example.com or 98765 43210"
                  value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
              </div>
              <Button type="submit" className="h-11 w-full rounded-xl font-bold">Send reset code</Button>
            </form>
          </>
        )}

        {step === 2 && (
          <>
            <p className="mt-2 text-sm text-muted-foreground">
              Enter the 6-digit code we sent to <span className="font-semibold text-foreground">{identifier}</span>.
            </p>
            <form onSubmit={(e) => { e.preventDefault(); setStep(3); }} className="mt-6 space-y-4">
              <Input inputMode="numeric" autoComplete="one-time-code" maxLength={6} aria-label="6-digit code"
                className="otp h-12 text-center text-lg font-bold" placeholder={"•".repeat(6)}
                value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
              <Button type="submit" className="h-11 w-full rounded-xl font-bold" disabled={code.length < 6}>Verify code</Button>
            </form>
          </>
        )}

        {step === 3 && (
          <>
            <p className="mt-2 text-sm text-muted-foreground">Choose a new password for your account.</p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                toast("Password updated — sign in with your new password", "success");
                navigate(paths.signIn);
              }}
              className="mt-6 space-y-4"
            >
              <div className="space-y-1.5">
                <label className="text-sm font-semibold" htmlFor="rp-new">New password</label>
                <Input id="rp-new" type="password" className="h-11" placeholder="At least 8 characters" minLength={8}
                  value={password} onChange={(e) => setPassword(e.target.value)} required />
              </div>
              <Button type="submit" className="h-11 w-full rounded-xl font-bold">Save new password</Button>
            </form>
          </>
        )}

        <p className="mt-8 text-center text-sm text-muted-foreground">
          Remembered it?{" "}
          <Link to={paths.signIn} className="font-semibold text-primary hover:underline">Sign in</Link>
        </p>
      </div>
    </div>
  );
}

import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { RoleMismatchError, useLogin } from "@/api/identity";
import { homeFor } from "@/auth/AuthContext";
import type { Role } from "@/auth/session";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Logo } from "@/components/ui/Logo";
import { cn } from "@/lib/cn";
import { paths } from "@/routes";

export default function SignIn() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useLogin();
  const [role, setRole] = useState<Role>("customer");
  const [showPassword, setShowPassword] = useState(false);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate(
      // "98765 43210" is typed with a space; vendor user ids never contain one.
      { userId: identifier.replace(/\s/g, ""), password, role, remember },
      {
        onSuccess: () => {
          // Back to the page that bounced them here, if it belongs to this role.
          const from = (location.state as { from?: string } | null)?.from;
          const home = homeFor(role);
          navigate(from?.startsWith(home) ? from : home, { replace: true });
        },
        // Switch to the account's real role, so signing in again just works.
        onError: (err) => {
          if (err instanceof RoleMismatchError) setRole(err.actual);
        },
      },
    );
  };

  const error =
    login.error instanceof ApiError || login.error instanceof RoleMismatchError
      ? login.error.message
      : login.error
        ? "Could not reach the server. Check your connection and try again."
        : null;

  return (
    <div className="signin-backdrop flex min-h-full flex-col items-center justify-center px-4 py-10">
      <div className="glass w-full max-w-md rounded-3xl border border-card/40 p-6 shadow-xl sm:p-7">
        <Logo title="Loan Assistance System" subtitle="SECURE ACCESS PORTAL" />
        <h1 className="mt-7 text-2xl font-extrabold tracking-tight">Sign in</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Use your registered mobile number or user ID and password to access your dashboard.
        </p>

        <div className="mt-5 grid grid-cols-2 gap-1 rounded-2xl bg-info/70 p-1.5">
          {(["customer", "bd"] as const).map((r) => (
            <button
              key={r}
              type="button"
              aria-pressed={role === r}
              onClick={() => { setRole(r); login.reset(); }}
              className={cn(
                "flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold leading-tight transition-all",
                role === r ? "bg-card text-accent-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon name={r === "customer" ? "user" : "idcard"} className="h-4 w-4 shrink-0" />
              {r === "customer" ? "Customer" : "Business Associate Executive"}
            </button>
          ))}
        </div>

        <form onSubmit={onSubmit} className="mt-5">
          <label className="text-sm font-semibold" htmlFor="identifier">User ID or mobile number</label>
          <Input id="identifier" icon="userround" className="mt-1.5 h-11 bg-card/80" placeholder="e.g. 9876543210 or Executive ID"
            autoComplete="username" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />

          <div className="mt-4 flex items-center justify-between">
            <label className="text-sm font-semibold" htmlFor="password">Password</label>
            <Link to={paths.resetPassword} className="text-sm font-semibold text-primary hover:underline">Reset password?</Link>
          </div>
          <div className="relative mt-1.5">
            <Input id="password" icon="lock" type={showPassword ? "text" : "password"} className="h-11 bg-card/80 pr-11" placeholder="Enter password"
              autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button type="button" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? "Hide password" : "Show password"}
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground">
              <Icon name={showPassword ? "eyeoff" : "eye"} className="h-5 w-5" />
            </button>
          </div>

          <label className="mt-4 flex items-center gap-2.5 text-sm">
            <input type="checkbox" className="check" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Keep me signed in
          </label>

          {error && (
            <p role="alert" className="mt-4 rounded-xl bg-destructive/10 px-3 py-2.5 text-sm font-medium text-destructive">
              {error}
            </p>
          )}

          <Button type="submit" size="lg" disabled={login.isPending} className="mt-5 w-full font-bold">
            {login.isPending ? "Signing in…" : <>Sign in as {role === "customer" ? "Customer" : "BD"} <Icon name="right" /></>}
          </Button>
        </form>

        {role === "customer" && (
          <div className="mt-5 flex items-start gap-2.5 rounded-xl bg-accent px-4 py-3 text-xs text-accent-foreground">
            <Icon name="info" className="mt-px h-4 w-4 shrink-0" />
            New customer? Your BD creates your account and sends your login details by SMS.
          </div>
        )}
      </div>
      <p className="mt-6 text-center text-xs font-semibold uppercase tracking-wider text-navy-foreground/70">
        Use the user ID registered with LAS.
      </p>
    </div>
  );
}

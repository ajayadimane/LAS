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
    <div className="flex min-h-full justify-center bg-background px-4 py-12">
      <div className="w-full max-w-sm">
        <Logo />
        <h1 className="mt-8 text-3xl font-extrabold tracking-tight">Sign in</h1>
        <p className="mt-2 text-sm text-muted-foreground">Use your user ID (usually your registered mobile number) and password.</p>

        <div className="mt-6 grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1">
          {(["customer", "bd"] as const).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => { setRole(r); login.reset(); }}
              className={cn(
                "rounded-xl px-4 py-2.5 text-sm font-semibold transition-all",
                role === r ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {r === "customer" ? "Customer" : "Business Associate Executive"}
            </button>
          ))}
        </div>

        <form onSubmit={onSubmit} className="mt-5 rounded-2xl border bg-card p-5 shadow-sm">
          <label className="text-sm font-semibold" htmlFor="identifier">User ID or mobile number</label>
          <div className="relative mt-1.5">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"><Icon name="userround" /></span>
            <Input id="identifier" className="h-11 pl-9" placeholder="98765 43210" autoComplete="username"
              value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
          </div>

          <div className="mt-4 flex items-center justify-between">
            <label className="text-sm font-semibold" htmlFor="password">Password</label>
            <Link to={paths.resetPassword} className="text-sm font-semibold text-primary hover:underline">Reset password</Link>
          </div>
          <div className="relative mt-1.5">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"><Icon name="lock" /></span>
            <Input id="password" type={showPassword ? "text" : "password"} className="h-11 pl-9 pr-20" placeholder="Enter password" autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button type="button" onClick={() => setShowPassword((v) => !v)}
              className="absolute right-3 top-1/2 flex -translate-y-1/2 items-center gap-1 text-sm font-semibold text-primary">
              <Icon name={showPassword ? "eyeoff" : "eye"} /> {showPassword ? "Hide" : "Show"}
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

          <Button type="submit" disabled={login.isPending} className="mt-5 h-11 w-full rounded-xl font-bold">
            {login.isPending ? "Signing in…" : role === "customer" ? "Sign in as customer" : "Sign in as BD"}
          </Button>
        </form>

        <p className="mt-16 text-center text-sm text-muted-foreground">
          {role === "customer"
            ? "New customer? Your BD creates your account and sends your login details by SMS."
            : "Use the user ID registered with LAS."}
        </p>
      </div>
    </div>
  );
}

import { useHealth } from "@/api/health";
import { useSession } from "@/auth/AuthContext";
import { clearSession } from "@/auth/session";
import { Logo } from "@/components/ui/Logo";
import { StatusPill } from "@/components/ui/StatusPill";

/** Stand-in for screens not ported yet. Shows the session and whether the middleware is reachable. */
export default function Placeholder({ title }: { title: string }) {
  const health = useHealth();
  const session = useSession();
  return (
    <div className="flex min-h-full justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md">
        <Logo />
        <h1 className="mt-8 text-3xl font-extrabold tracking-tight">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">This screen hasn't been ported from the demo yet.</p>
        <div className="mt-6 flex items-center gap-2 text-sm">
          Middleware:
          {health.isPending ? <StatusPill tone="neutral">Checking…</StatusPill>
            : health.isError ? <StatusPill tone="warning">Not reachable</StatusPill>
            : <StatusPill tone="success">Connected</StatusPill>}
        </div>
        {session && (
          <p className="mt-3 text-sm text-muted-foreground">
            Signed in as {session.role === "bd" ? "BD" : "customer"}{" "}
            <span className="font-semibold text-foreground">{session.userId}</span>
            {session.expiresAt && <> until {new Date(session.expiresAt * 1000).toLocaleString("en-IN")}</>}
          </p>
        )}
        <button onClick={clearSession} className="mt-8 text-sm font-semibold text-primary hover:underline">
          Sign out
        </button>
      </div>
    </div>
  );
}

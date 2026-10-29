import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { paths } from "@/routes";
import { clearSession, getSession, subscribe, type Role } from "./session";

export const homeFor = (role: Role) => (role === "customer" ? paths.customerDashboard : paths.bdDashboard);

export function useSession() {
  return useSyncExternalStore(subscribe, getSession);
}

/** Signs the user out the moment the vendor token expires. */
export function SessionExpiryWatcher() {
  const session = useSession();
  useEffect(() => {
    if (!session?.expiresAt) return;
    const ms = session.expiresAt * 1000 - Date.now();
    // setTimeout overflows past ~24.8 days; tokens live hours, but guard anyway.
    const timer = setTimeout(clearSession, Math.min(Math.max(ms, 0), 2 ** 31 - 1));
    return () => clearTimeout(timer);
  }, [session]);
  return null;
}

/** Renders children only for a signed-in user with this role. */
export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const session = useSession();
  const location = useLocation();
  if (!session) return <Navigate to={paths.signIn} replace state={{ from: location.pathname }} />;
  if (session.role !== role) return <Navigate to={homeFor(session.role)} replace />;
  return <>{children}</>;
}

/** For the sign-in and reset pages: a signed-in user goes straight home. */
export function RedirectIfSignedIn({ children }: { children: ReactNode }) {
  const session = useSession();
  return session ? <Navigate to={homeFor(session.role)} replace /> : <>{children}</>;
}

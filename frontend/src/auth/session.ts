/**
 * The signed-in user, kept outside React so the API client can read the token.
 *
 * "Keep me signed in" puts the session in localStorage (survives closing the
 * browser); otherwise sessionStorage (gone when the tab closes). Either way it
 * is dropped once the vendor token's `exp` passes, since there is no refresh.
 */

export type Role = "customer" | "bd";

export interface Session {
  /** Vendor JWT, sent as `Authorization: Bearer` on every middleware call. */
  token: string;
  /**
   * The userId that signed in. For a BD this is the bdId (the brokerId their
   * customers are registered under); for a customer it is the custId.
   */
  userId: string;
  role: Role;
  /** Token expiry in Unix seconds, or null if the token carries none. */
  expiresAt: number | null;
}

const KEY = "las.session";
const listeners = new Set<() => void>();

const isExpired = (s: Session) => s.expiresAt !== null && s.expiresAt * 1000 <= Date.now();

function read(storage: Storage): Session | null {
  try {
    const raw = storage.getItem(KEY);
    const s = raw ? (JSON.parse(raw) as Session) : null;
    return s && s.token && s.userId && !isExpired(s) ? s : null;
  } catch {
    return null;
  }
}

function wipe() {
  for (const storage of [localStorage, sessionStorage]) {
    try {
      storage.removeItem(KEY);
    } catch {
      /* storage blocked: nothing to remove */
    }
  }
}

let current: Session | null = read(sessionStorage) ?? read(localStorage);

export const getSession = () => current;

export function setSession(session: Session, remember: boolean) {
  wipe();
  try {
    (remember ? localStorage : sessionStorage).setItem(KEY, JSON.stringify(session));
  } catch {
    /* storage blocked: the session still lives in memory for this tab */
  }
  current = session;
  listeners.forEach((fn) => fn());
}

export function clearSession() {
  wipe();
  if (current === null) return;
  current = null;
  listeners.forEach((fn) => fn());
}

export function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

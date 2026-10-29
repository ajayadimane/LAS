import { useMutation } from "@tanstack/react-query";
import { setSession, type Role } from "@/auth/session";
import { api } from "./client";

/** POST /api/v1/identity/login response. Mirrors app/schemas/identity.py. */
export interface LoginResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  /** From the token's groupId: LAS.admin = BD, LAS.enduser = customer. */
  role: Role;
  expires_in: number | null;
  expires_at: number | null;
  transaction_id: string | null;
  correlation_id: string | null;
}

export interface LoginInput {
  userId: string;
  password: string;
  role: Role;
  remember: boolean;
}

const ROLE_NAMES: Record<Role, string> = { bd: "BD", customer: "Customer" };

/** The account's real role differs from the one chosen on the sign-in screen. No session is stored. */
export class RoleMismatchError extends Error {
  constructor(readonly actual: Role) {
    super(`This is a ${ROLE_NAMES[actual]} account. Choose "${ROLE_NAMES[actual]}" above and sign in again.`);
    this.name = "RoleMismatchError";
  }
}

/**
 * Logs in and stores the session (token + bdId/custId) on success. The role
 * comes from the account (the token's group), never from the screen: if the
 * user picked the other one, nothing is stored and RoleMismatchError says so.
 */
export const useLogin = () =>
  useMutation({
    mutationFn: async ({ userId, password, role }: LoginInput) => {
      const res = await api.post<LoginResponse>("identity/login", { user_id: userId, password });
      if (res.role !== role) throw new RoleMismatchError(res.role);
      return res;
    },
    onSuccess: (res, { remember }) =>
      setSession({ token: res.access_token, userId: res.user_id, role: res.role, expiresAt: res.expires_at }, remember),
  });

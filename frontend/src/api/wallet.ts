import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

/** Mirrors app/schemas/wallet.py. A null amount means the vendor left it out, not zero. */
export interface WalletBalance {
  wallet_id: string;
  tag_id: string;
  total_amount: number | null;
  available_amount: number | null;
  locked_amount: number | null;
}

/** The signed-in user's wallet; its id is their userId (BD id or customer id). */
export const useWalletBalance = (userId: string) =>
  useQuery({
    queryKey: ["wallet", userId, "balance"],
    queryFn: () => api.get<WalletBalance>(`wallet/balance?wallet_id=${encodeURIComponent(userId)}`),
  });

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export type Health = Record<string, unknown>;

/** GET /health on the middleware. Used to confirm the dev proxy is wired up. */
export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health"), retry: false });

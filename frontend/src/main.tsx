import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { getSession, subscribe } from "./auth/session";
import { ToastProvider } from "./components/ui/Toast";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
});

// On sign-out (or a dead token) drop every cached response, so the next user
// to sign in on this browser never sees the previous user's customers.
subscribe(() => {
  if (!getSession()) queryClient.clear();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* BASE_URL is Vite's `base` (VITE_BASE_PATH), e.g. "/las/". */}
      <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/+$/, "")}>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);

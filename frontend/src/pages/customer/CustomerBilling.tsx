import { useNavigate } from "react-router-dom";
import { useMyProfile, useMyServices } from "@/api/customer";
import { useSession } from "@/auth/AuthContext";
import { clearSession } from "@/auth/session";
import { AppSidebar, type NavItem } from "@/components/layout/AppSidebar";
import { PaymentsTable, usePayments } from "@/components/PaymentsTable";
import { StatCard } from "@/components/ui/StatCard";
import { fullName, phone } from "@/lib/customer";
import { inr } from "@/lib/format";
import { paths } from "@/routes";

/** Billing & invoices: the payment details of every service the customer has opted into or requested. */
export default function CustomerBilling() {
  const navigate = useNavigate();
  const session = useSession()!; // RequireRole guarantees a customer session
  const profile = useMyProfile();
  const state = useMyServices();
  const { totalPaid, payLater, awaiting } = usePayments(state.data);
  const stat = (v: string | number) => (state.isPending ? "–" : v);

  const p = profile.data;
  const nav: NavItem[] = [
    { label: "Dashboard", icon: "grid", onClick: () => navigate(paths.customerDashboard) },
    { label: "Wallet", icon: "wallet", onClick: () => navigate(`${paths.customerDashboard}?open=wallet`) },
    { label: "Billing & invoices", icon: "receipt", active: true },
    { label: "Profile", icon: "user", onClick: () => navigate(`${paths.customerDashboard}?open=profile`) },
  ];

  return (
    <div className="flex min-h-full flex-col bg-background lg:flex-row">
      <AppSidebar roleLabel="Customer" userName={p ? fullName(p) : "Customer"} userPhone={p ? phone(p) : `ID ${session.userId}`}
        items={nav} onSignOut={clearSession} />

      <main className="min-w-0 flex-1 px-4 py-8 sm:px-6 lg:px-10 lg:py-10">
        <h1 className="text-3xl font-extrabold tracking-tight">Billing & invoices</h1>
        <p className="mt-2 text-sm text-muted-foreground">Payment details for each of your services.</p>

        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
          <StatCard label="Total paid" value={stat(inr(totalPaid))} />
          <StatCard label="Paid through Pay later" value={stat(payLater)} tone="warning" />
          <StatCard label="Awaiting payment" value={stat(awaiting)} tone="info" />
        </div>

        <section className="mt-8 overflow-hidden rounded-2xl border bg-card">
          <div className="px-5 py-5">
            <h2 className="text-lg font-bold">Payments</h2>
          </div>
          <PaymentsTable query={state} viewer="customer" />
        </section>
      </main>
    </div>
  );
}

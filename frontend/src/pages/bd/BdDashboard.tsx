import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useBdCustomers, type BdCustomer } from "@/api/bd";
import { useWalletBalance } from "@/api/wallet";
import { useSession } from "@/auth/AuthContext";
import { clearSession } from "@/auth/session";
import { AppSidebar, type NavItem } from "@/components/layout/AppSidebar";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { StatCard } from "@/components/ui/StatCard";
import { StatusPill } from "@/components/ui/StatusPill";
import { cn } from "@/lib/cn";
import { fullName, phone } from "@/lib/customer";
import { readDocStatus, readPaymentMode, readPaymentStatus } from "@/lib/customerStatus";
import { initials, inr } from "@/lib/format";
import { paths } from "@/routes";

type Filter = "all" | "awaiting" | "not-uploaded" | "pending";

const matches = (c: BdCustomer, filter: Filter) =>
  filter === "all" ||
  (filter === "awaiting" && readDocStatus(c.doc_status).bucket === "awaiting") ||
  (filter === "not-uploaded" && readDocStatus(c.doc_status).bucket === "not-uploaded") ||
  (filter === "pending" && readPaymentStatus(c.payment_status).bucket === "pending");

const amount = (n: number | null) => (n === null ? "—" : inr(n));

// Sidebar entries other than Dashboard have no screen yet (none in the demo either).
const NAV: NavItem[] = [
  { label: "Dashboard", icon: "grid", active: true },
  { label: "Customers", icon: "users" },
  { label: "Reports", icon: "report" },
  { label: "Billing & invoices", icon: "receipt" },
  { label: "Commission", icon: "coins" },
];

export default function BdDashboard() {
  const navigate = useNavigate();
  const session = useSession()!; // RequireRole guarantees a BD session
  const query = useBdCustomers(session.userId);
  const wallet = useWalletBalance(session.userId);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");

  const customers = useMemo(() => query.data?.customers ?? [], [query.data]);
  const counts = useMemo(
    () => ({
      all: customers.length,
      awaiting: customers.filter((c) => matches(c, "awaiting")).length,
      "not-uploaded": customers.filter((c) => matches(c, "not-uploaded")).length,
      pending: customers.filter((c) => matches(c, "pending")).length,
      requests: customers.filter((c) => c.requests.length > 0).length,
    }),
    [customers],
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const qDigits = q.replace(/\D/g, "");
    return customers.filter(
      (c) =>
        matches(c, filter) &&
        (!q || fullName(c).toLowerCase().includes(q) || (qDigits !== "" && (c.mobile_number ?? "").includes(qDigits))),
    );
  }, [customers, filter, search]);

  const chips: Array<[Filter, string]> = [
    ["all", `All (${counts.all})`],
    ["awaiting", `Docs awaiting report (${counts.awaiting})`],
    ["not-uploaded", `Docs not uploaded (${counts["not-uploaded"]})`],
    ["pending", `Payment pending (${counts.pending})`],
  ];

  const stat = (n: number) => (query.isPending ? "–" : n);

  return (
    <div className="flex min-h-full flex-col bg-background lg:flex-row">
      <AppSidebar roleLabel="BD workspace" userName="Business Developer" userPhone={`ID ${session.userId}`} items={NAV} onSignOut={clearSession} />

      <main className="min-w-0 flex-1 px-4 py-8 sm:px-6 lg:px-10 lg:py-10">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight">Your customers</h1>
            <p className="mt-2 text-sm text-muted-foreground">Track every customer from onboarding to report delivery.</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button variant="outline" className="h-11 rounded-xl" disabled title="Not available yet">
              <Icon name="download" /> Get reports
            </Button>
            <Button className="h-11 rounded-xl" onClick={() => navigate(paths.bdAddCustomer)}>
              <Icon name="plus" /> Add new customer
            </Button>
          </div>
        </div>

        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Customers onboarded" value={stat(counts.all)} />
          <StatCard label="Awaiting your authorization" value={stat(counts.requests)} tone="info" />
          <StatCard label="Payment pending" value={stat(counts.pending)} tone="warning" />
          <div className="rounded-2xl bg-primary p-5 text-primary-foreground">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm font-semibold">Wallet</p>
              {wallet.isError && (
                <button className="text-xs underline opacity-80 hover:opacity-100" onClick={() => wallet.refetch()}>
                  Retry
                </button>
              )}
            </div>
            <p className="mt-2 text-2xl font-extrabold tracking-tight">
              {wallet.isPending ? "–" : wallet.isError ? "Unavailable" : amount(wallet.data.available_amount)}
            </p>
            {wallet.data && (
              <p className="text-xs opacity-80">
                Available · total {amount(wallet.data.total_amount)}
                {!!wallet.data.locked_amount && ` · locked ${amount(wallet.data.locked_amount)}`}
              </p>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button size="sm" variant="ghost" disabled title="Not available yet"
                className="rounded-lg border border-primary-foreground/50 bg-transparent text-primary-foreground hover:bg-primary-foreground/10">
                View account
              </Button>
            </div>
          </div>
        </div>

        <section className="mt-6 overflow-hidden rounded-2xl border bg-card">
          <div className="flex flex-wrap items-center gap-2 p-5">
            {chips.map(([value, label]) => (
              <button key={value} onClick={() => setFilter(value)}
                className={cn("rounded-full border px-4 py-2 text-xs font-semibold transition-colors",
                  filter === value ? "border-navy bg-navy text-navy-foreground" : "bg-card hover:bg-muted")}>
                {label}
              </button>
            ))}
            <div className="relative min-w-[220px] flex-1 lg:ml-auto">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"><Icon name="search" /></span>
              <Input className="h-10 bg-muted/50 pl-9" placeholder="Search name or phone" value={search}
                onChange={(e) => setSearch(e.target.value)} aria-label="Search customers" />
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="border-y bg-muted/60 text-left text-xs font-semibold text-muted-foreground">
                <tr>
                  <th className="px-5 py-4">Name</th>
                  <th className="px-5 py-4">Services opted</th>
                  <th className="px-5 py-4">Payment status</th>
                  <th className="px-5 py-4">Docs uploaded</th>
                  <th className="px-5 py-4">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {query.isPending ? (
                  <Message>Loading customers…</Message>
                ) : query.isError ? (
                  <Message>
                    <p>{query.error.message}</p>
                    <Button variant="outline" size="sm" className="mt-3 rounded-lg" onClick={() => query.refetch()}>Try again</Button>
                  </Message>
                ) : customers.length === 0 ? (
                  <Message>
                    <p>No customers yet.</p>
                    <Button size="sm" className="mt-3 rounded-lg" onClick={() => navigate(paths.bdAddCustomer)}>
                      <Icon name="plus" /> Add your first customer
                    </Button>
                  </Message>
                ) : visible.length === 0 ? (
                  <Message>No customers match this view.</Message>
                ) : (
                  visible.map((c) => <CustomerRow key={c.person_id ?? fullName(c)} c={c} />)
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}

function CustomerRow({ c }: { c: BdCustomer }) {
  const navigate = useNavigate();
  const name = fullName(c);
  const pay = readPaymentStatus(c.payment_status);
  const mode = readPaymentMode(c.payment_mode);
  const docs = readDocStatus(c.doc_status);
  const id = c.person_id;

  return (
    <tr>
      <td className="px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-bold">{initials(name)}</span>
          <div>
            <p className="font-bold">{name}</p>
            <p className="text-xs text-muted-foreground">{phone(c)}</p>
          </div>
        </div>
      </td>
      <td className="px-5 py-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-navy text-sm font-bold text-navy-foreground">{c.services.length}</span>
          <span className="text-xs text-muted-foreground">{c.services.map((s) => s.name).join(", ") || "None yet"}</span>
        </div>
        {c.requests.length > 0 && (
          <button className="mt-2" onClick={() => navigate(paths.bdCustomer(id!))} disabled={!id}>
            <StatusPill tone="warning">
              {c.requests.length} {c.requests.length === 1 ? "request" : "requests"} to authorize
            </StatusPill>
          </button>
        )}
      </td>
      <td className="px-5 py-4">
        <StatusPill tone={pay.tone}>{pay.label}</StatusPill>
        {mode && <p className="mt-1 text-xs text-muted-foreground">{mode}</p>}
      </td>
      <td className="px-5 py-4"><StatusPill tone={docs.tone}>{docs.label}</StatusPill></td>
      <td className="px-5 py-4">
        {id && (
          <div className="flex items-center gap-4">
            <button className="font-medium text-primary hover:underline" onClick={() => navigate(paths.bdCustomer(id))}>Preview</button>
            <button aria-label={`Edit ${name}`} className="rounded p-1 hover:bg-muted" onClick={() => navigate(`${paths.bdCustomer(id)}?edit=1`)}>
              <Icon name="pencil" />
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

function Message({ children }: { children: ReactNode }) {
  return (
    <tr>
      <td colSpan={5} className="px-5 py-10 text-center text-muted-foreground">{children}</td>
    </tr>
  );
}

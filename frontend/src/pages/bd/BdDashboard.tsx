import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useBdCustomers, type BdCustomer } from "@/api/bd";
import { useWalletBalance } from "@/api/wallet";
import { useSession } from "@/auth/AuthContext";
import { clearSession } from "@/auth/session";
import { AppSidebar, type NavItem } from "@/components/layout/AppSidebar";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { ComingSoon } from "@/components/ui/ComingSoon";
import { FilterChip } from "@/components/ui/FilterChip";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { StatusPill } from "@/components/ui/StatusPill";
import { fullName, phone } from "@/lib/customer";
import { readDocsColumn, readPaymentMode, readPaymentStatus } from "@/lib/customerStatus";
import { initials, inr } from "@/lib/format";
import { paths } from "@/routes";

type Filter = "all" | "awaiting" | "not-uploaded" | "pending";
type FilterTone = "navy" | "info" | "warning" | "danger";

const matches = (c: BdCustomer, filter: Filter) =>
  filter === "all" ||
  (filter === "awaiting" && readDocsColumn(c.doc_status, c.services).bucket === "awaiting") ||
  (filter === "not-uploaded" && readDocsColumn(c.doc_status, c.services).bucket === "not-uploaded") ||
  (filter === "pending" && readPaymentStatus(c.payment_status).bucket === "pending");

const amount = (n: number | null) => (n === null ? "—" : inr(n));

// Sidebar entries other than Dashboard have no screen yet: shown, but disabled.
const NAV: NavItem[] = [
  { label: "Dashboard", icon: "grid", active: true },
  { label: "Customers", icon: "users", disabled: true },
  { label: "Reports", icon: "report", disabled: true },
  { label: "Billing & invoices", icon: "receipt", disabled: true },
  { label: "Commission", icon: "coins", disabled: true },
];

const PAGE_SIZE = 5;

export default function BdDashboard() {
  const navigate = useNavigate();
  const session = useSession()!; // RequireRole guarantees a BD session
  const query = useBdCustomers(session.userId);
  const wallet = useWalletBalance(session.userId);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

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

  // Back to page 1 whenever the view changes; the vendor has no paging, so this is client-side.
  useEffect(() => setPage(1), [filter, search]);
  const pageRows = visible.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const chips: Array<[Filter, string, FilterTone]> = [
    ["all", "All", "navy"],
    ["awaiting", "Docs awaiting report", "info"],
    ["not-uploaded", "Docs not uploaded", "warning"],
    ["pending", "Payment pending", "danger"],
  ];

  const stat = (n: number) => (query.isPending ? "–" : n);

  return (
    <div className="flex min-h-full flex-col bg-background lg:flex-row">
      <AppSidebar roleLabel="BD workspace" userName="Business Developer" userPhone={`ID ${session.userId}`} items={NAV} onSignOut={clearSession} />

      <main className="min-w-0 flex-1 px-4 py-8 sm:px-6 lg:px-10 lg:py-8">
        <PageHeader
          title="Welcome back"
          subtitle={<span className="mt-1 inline-block rounded-md bg-muted px-2 py-0.5 text-xs font-semibold text-foreground">BD ID: {session.userId}</span>}
          actions={
            <>
              <ComingSoon>
                <Button variant="outline" disabled className="h-10 rounded-full border-danger-foreground/40 bg-danger/40 text-danger-foreground">
                  <Icon name="bell" /> Escalation
                </Button>
              </ComingSoon>
              <ComingSoon>
                <Button variant="outline-primary" disabled className="h-10 rounded-full">
                  <Icon name="download" /> Get reports
                </Button>
              </ComingSoon>
            </>
          }
        />

        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          <KpiCard label="Total Customers Onboarded" value={stat(counts.all)} tone="warning" icon="usercheck"
            caption="All active client portfolios" dot />
          <KpiCard label="Awaiting Your Authorization" value={stat(counts.requests)} tone="info" icon="shieldcheck"
            caption="Requires BD sign-off" dot />
          <KpiCard
            label="Total Service Fee Collected"
            tone="success"
            icon="rupee"
            value={wallet.isPending ? "–" : wallet.isError ? <span className="text-xl">Unavailable</span> : amount(wallet.data.available_amount)}
            caption={
              wallet.isError ? (
                <button className="underline" onClick={() => wallet.refetch()}>Retry</button>
              ) : wallet.data ? (
                <>
                  Available · Total {amount(wallet.data.total_amount)}
                  {!!wallet.data.locked_amount && ` · locked ${amount(wallet.data.locked_amount)}`}
                </>
              ) : null
            }
            action={
              <ComingSoon>
                <span className="flex items-center gap-1 whitespace-nowrap text-sm font-semibold opacity-60">View Account <Icon name="right" className="h-3.5 w-3.5" /></span>
              </ComingSoon>
            }
          />
        </div>

        <div className="my-6 flex justify-center">
          <Button size="lg" className="cta-pop" onClick={() => navigate(paths.bdAddCustomer)}>
            <Icon name="plus" /> Add New Customer
          </Button>
        </div>

        <section className="overflow-hidden rounded-2xl border bg-card shadow-card">
          <div className="flex flex-wrap items-center gap-2.5 p-5">
            {chips.map(([value, label, tone]) => (
              <FilterChip key={value} label={label} count={query.isPending ? undefined : counts[value]} tone={tone}
                active={filter === value} onClick={() => setFilter(value)} />
            ))}
            <div className="min-w-[180px] flex-1 lg:ml-auto">
              <Input icon="search" className="h-10 bg-muted/50" placeholder="Search name or phone…" value={search}
                onChange={(e) => setSearch(e.target.value)} aria-label="Search customers" />
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="bg-accent/70 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-6 py-4">Name</th>
                  <th className="px-5 py-4">Services opted</th>
                  <th className="px-5 py-4">Payment status</th>
                  <th className="px-5 py-4">Docs uploaded</th>
                  <th className="px-6 py-4 text-right">Actions</th>
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
                  pageRows.map((c) => <CustomerRow key={c.person_id ?? fullName(c)} c={c} />)
                )}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={visible.length} onPage={setPage} noun="customers" />
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
  const docs = readDocsColumn(c.doc_status, c.services);
  const id = c.person_id;

  return (
    <tr className="even:bg-muted/30">
      <td className="px-6 py-4">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border bg-muted text-xs font-bold">{initials(name)}</span>
          <div>
            <p className="font-bold">{name}</p>
            <p className="text-xs text-muted-foreground">{phone(c)}</p>
          </div>
        </div>
      </td>
      <td className="px-5 py-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-navy text-xs font-bold text-navy-foreground">{c.services.length}</span>
          <span className="text-sm">{c.services.map((s) => s.name).join(" + ") || <span className="text-muted-foreground">None yet</span>}</span>
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
      <td className="px-5 py-4"><StatusPill tone={docs.tone} dot>{docs.label}</StatusPill></td>
      <td className="px-6 py-4">
        {id && (
          <div className="flex items-center justify-end gap-3">
            <button className="flex items-center gap-1.5 rounded-lg px-2 py-1 font-medium hover:bg-muted" onClick={() => navigate(paths.bdCustomer(id))}>
              <Icon name="eye" /> Preview
            </button>
            <button aria-label={`Edit ${name}`} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" onClick={() => navigate(`${paths.bdCustomer(id)}?edit=1`)}>
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

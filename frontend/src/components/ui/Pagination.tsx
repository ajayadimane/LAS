import { cn } from "@/lib/cn";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  noun?: string;
}

/** "Showing 1-5 of 24 customers" with Previous / pages / Next. Client-side only. */
export function Pagination({ page, pageSize, total, onPage, noun = "items" }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  // At most 5 page buttons around the current page.
  const start = Math.max(1, Math.min(page - 2, pages - 4));
  const shown = Array.from({ length: Math.min(5, pages) }, (_, i) => start + i);
  const btn = "h-8 min-w-8 rounded-lg border px-2.5 text-sm font-medium transition-colors disabled:opacity-50";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-5 py-4 text-sm text-muted-foreground">
      <p>Showing <b className="text-foreground">{from}–{to}</b> of <b className="text-foreground">{total}</b> {noun}</p>
      <nav aria-label="Pagination" className="flex items-center gap-1.5">
        <button className={cn(btn, "bg-card hover:bg-muted")} disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        {shown.map((n) => (
          <button key={n} aria-current={n === page ? "page" : undefined} onClick={() => onPage(n)}
            className={cn(btn, n === page ? "border-navy bg-navy text-navy-foreground" : "bg-card text-foreground hover:bg-muted")}>
            {n}
          </button>
        ))}
        <button className={cn(btn, "bg-card text-foreground hover:bg-muted")} disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </nav>
    </div>
  );
}

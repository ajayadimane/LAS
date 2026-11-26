import { useState } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Logo } from "@/components/ui/Logo";

export interface NavItem {
  label: string;
  icon: IconName;
  active?: boolean;
  onClick?: () => void;
  /** Shown but not usable yet (no backend); gets a "Coming soon" hint. */
  disabled?: boolean;
}

interface AppSidebarProps {
  roleLabel: string;
  userName: string;
  userPhone: string;
  items: NavItem[];
  onSignOut: () => void;
}

/** Fixed navy sidebar on desktop (lg+); top bar with slide-out drawer below that. */
export function AppSidebar({ roleLabel, userName, userPhone, items, onSignOut }: AppSidebarProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const nav = (
    <nav className="mt-8 flex flex-col gap-1.5 px-4">
      {items.map((item) => (
        <button
          key={item.label}
          disabled={item.disabled}
          title={item.disabled ? "Coming soon" : undefined}
          aria-current={item.active ? "page" : undefined}
          onClick={() => {
            setDrawerOpen(false);
            item.onClick?.();
          }}
          className={cn(
            "flex items-center gap-3 rounded-xl border px-4 py-2.5 text-left text-sm font-medium transition-colors",
            item.active
              ? "border-primary/50 bg-primary/20 text-navy-foreground"
              : "border-transparent text-navy-muted hover:bg-navy-foreground/10 hover:text-navy-foreground",
            item.disabled && "cursor-not-allowed opacity-60 hover:bg-transparent hover:text-navy-muted",
          )}
        >
          <Icon name={item.icon} className="h-[18px] w-[18px]" />
          {item.label}
        </button>
      ))}
    </nav>
  );

  const footer = (
    <div className="mt-auto p-3 pt-8">
      <div className="rounded-xl bg-navy-foreground/5 p-3.5">
        <p className="text-xs text-navy-muted">Signed in as</p>
        <div className="mt-1.5 flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Icon name="user" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-bold">{userName}</p>
            <p className="truncate text-xs font-semibold text-navy-muted">{userPhone}</p>
          </div>
        </div>
        <button
          onClick={onSignOut}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-navy-foreground/10 py-1.5 text-xs font-semibold transition-colors hover:bg-navy-foreground/20"
        >
          <Icon name="logout" className="h-3.5 w-3.5" /> Sign out
        </button>
      </div>
    </div>
  );

  return (
    <>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-navy text-navy-foreground lg:flex">
        <div className="px-5 pt-6">
          <Logo dark subtitle={roleLabel} />
        </div>
        {nav}
        {footer}
      </aside>

      <div className="mobile-bar sticky z-30 flex items-center justify-between bg-navy px-4 py-3 text-navy-foreground lg:hidden">
        <Logo dark />
        <button aria-label="Open menu" className="rounded-lg p-2 hover:bg-navy-foreground/10" onClick={() => setDrawerOpen(true)}>
          <Icon name="menu" className="h-5 w-5" />
        </button>
      </div>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setDrawerOpen(false)} />
          <aside className="drawer relative flex h-full w-64 flex-col bg-navy text-navy-foreground">
            <div className="flex items-start justify-between px-5 pt-6">
              <Logo dark subtitle={roleLabel} />
              <button aria-label="Close menu" className="rounded p-1 hover:bg-navy-foreground/10" onClick={() => setDrawerOpen(false)}>
                <Icon name="x" />
              </button>
            </div>
            {nav}
            {footer}
          </aside>
        </div>
      )}
    </>
  );
}

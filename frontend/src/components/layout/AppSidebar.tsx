import { useState } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Logo } from "@/components/ui/Logo";

export interface NavItem {
  label: string;
  icon: IconName;
  active?: boolean;
  onClick?: () => void;
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
    <nav className="mt-8 flex flex-col gap-1 px-3">
      {items.map((item) => (
        <button
          key={item.label}
          onClick={() => {
            setDrawerOpen(false);
            item.onClick?.();
          }}
          className={cn(
            "flex items-center gap-2.5 rounded-xl px-4 py-2.5 text-left text-sm font-medium transition-colors",
            item.active
              ? "bg-navy-foreground/10 text-navy-foreground"
              : "text-navy-muted hover:bg-navy-foreground/10 hover:text-navy-foreground",
          )}
        >
          <Icon name={item.icon} />
          {item.label}
        </button>
      ))}
    </nav>
  );

  const footer = (
    <div className="mt-auto px-5 pb-6 pt-8">
      <p className="text-xs text-navy-muted">Signed in as</p>
      <p className="mt-1 text-sm font-bold">{userName}</p>
      <p className="text-xs text-navy-muted">{userPhone}</p>
      <button
        onClick={onSignOut}
        className="mt-4 flex items-center gap-2 text-sm font-medium text-navy-muted transition-colors hover:text-navy-foreground"
      >
        <Icon name="logout" /> Sign out
      </button>
    </div>
  );

  return (
    <>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-navy text-navy-foreground lg:flex">
        <div className="px-5 pt-6">
          <Logo dark />
          <p className="mt-2 text-sm text-navy-muted">{roleLabel}</p>
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
              <div>
                <Logo dark />
                <p className="mt-2 text-sm text-navy-muted">{roleLabel}</p>
              </div>
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

import type { ReactNode } from "react";

/** Wraps a control that the design shows but no backend supports yet: disabled, with a tooltip. */
export function ComingSoon({ children, label = "Coming soon" }: { children: ReactNode; label?: string }) {
  return (
    <span className="group relative inline-flex" title={label}>
      {/* Pass a disabled control: it already looks faded, so no extra opacity here. */}
      <span className="pointer-events-none inline-flex" aria-disabled="true">{children}</span>
      <span role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-20 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-navy px-2 py-1 text-xs font-medium text-navy-foreground opacity-0 transition-opacity group-hover:opacity-100">
        {label}
      </span>
    </span>
  );
}

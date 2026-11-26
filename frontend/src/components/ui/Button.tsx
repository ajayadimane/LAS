import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const variants = {
  default: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline: "border bg-card hover:bg-muted",
  /** Primary-coloured outline: "+ Add services", "View Annexures". */
  "outline-primary": "border border-primary/60 bg-card text-primary hover:bg-accent",
  secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
  ghost: "hover:bg-muted",
};

const sizes = {
  md: "h-10 px-4 text-sm",
  sm: "h-9 px-3 text-sm",
  /** Page-level call to action, with the soft primary shadow. */
  lg: "h-12 px-6 text-sm rounded-xl shadow-cta",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
}

export function Button({ variant = "default", size = "md", className, type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "btn inline-flex items-center justify-center gap-2 whitespace-nowrap font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50",
        variants[variant], sizes[size], className,
      )}
      {...props}
    />
  );
}

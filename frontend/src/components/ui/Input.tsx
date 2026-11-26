import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Leading icon inside the field. */
  icon?: IconName;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(({ className, icon, ...props }, ref) => {
  const input = (
    <input
      ref={ref}
      className={cn("field w-full rounded-xl border border-input bg-card px-3 text-sm placeholder:text-muted-foreground", icon && "pl-10", className)}
      {...props}
    />
  );
  if (!icon) return input;
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground">
        <Icon name={icon} />
      </span>
      {input}
    </div>
  );
});
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn("field w-full rounded-xl border border-input bg-card p-3 text-sm placeholder:text-muted-foreground", className)}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn("field w-full rounded-xl border border-input bg-card px-3 text-sm placeholder:text-muted-foreground", className)}
      {...props}
    />
  ),
);
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

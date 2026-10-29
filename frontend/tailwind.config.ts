import type { Config } from "tailwindcss";

// Colours are oklch triplets defined as CSS variables in src/index.css,
// so light/dark themes swap without touching class names.
const tokens = [
  "background", "foreground", "card", "primary", "primary-foreground", "secondary", "secondary-foreground",
  "muted", "muted-foreground", "accent", "accent-foreground", "destructive", "border", "input", "ring",
  "navy", "navy-foreground", "navy-muted", "info", "info-foreground", "warning", "warning-foreground",
  "success", "success-foreground", "neutral", "neutral-foreground",
];

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      borderRadius: { lg: "0.75rem", xl: "1rem", "2xl": "1.25rem", "3xl": "1.5rem" },
      colors: Object.fromEntries(tokens.map((k) => [k, `oklch(var(--${k}) / <alpha-value>)`])),
    },
  },
  plugins: [],
} satisfies Config;

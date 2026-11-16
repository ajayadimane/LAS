/** ₹1,234.50 */
export const inr = (n: number) =>
  "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** ₹1,234 */
export const inrShort = (n: number) => "₹" + n.toLocaleString("en-IN");

/** Mirrors app/services/dpr_agentic/cam_snapshot.py's Money: converts a
 * lakh/crore-denominated figure to plain rupees for inr()/inrShort(). */
/** Python Decimal is serialized as a JSON string by Pydantic.
 * Never assume a financial API value is a JS number at runtime.
 * Return null for missing, malformed, or non-finite financial values. */
export function parseFinancialNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const raw = value.trim();
  // Accept standard Decimal JSON strings, including exponents. No commas or units.
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

export const moneyToRupees = (m: { amount: number | string; unit: "absolute" | "lakh" | "crore" }): number | null => {
  const amount = parseFinancialNumber(m.amount);
  if (amount === null) return null;
  const converted = amount * (m.unit === "crore" ? 1e7 : m.unit === "lakh" ? 1e5 : 1);
  return Number.isFinite(converted) ? converted : null;
};

/** "Ravi Kumar" -> "RK" */
export const initials = (name: string) =>
  name.split(" ").filter(Boolean).map((p) => p[0]).slice(0, 2).join("").toUpperCase();

/** "19 Oct 2026" */
export const todayStr = () =>
  new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

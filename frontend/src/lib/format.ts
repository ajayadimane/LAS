/** ₹1,234.50 */
export const inr = (n: number) =>
  "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** ₹1,234 */
export const inrShort = (n: number) => "₹" + n.toLocaleString("en-IN");

/** Mirrors app/services/dpr_agentic/cam_snapshot.py's Money: converts a
 * lakh/crore-denominated figure to plain rupees for inr()/inrShort(). */
export const moneyToRupees = (m: { amount: number; unit: "absolute" | "lakh" | "crore" }) =>
  m.amount * (m.unit === "crore" ? 1e7 : m.unit === "lakh" ? 1e5 : 1);

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

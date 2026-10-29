import type { BdCustomer } from "@/api/bd";

/** Anything with a person's name, phone and address: a BD's customer, or a customer's own profile. */
type Person = Pick<BdCustomer, "first_name" | "last_name" | "mobile_number" | "mobile_country_code" | "address">;

export const fullName = (c: Person) => [c.first_name, c.last_name].filter(Boolean).join(" ") || "Unnamed customer";

/** "+91 90000 00013" */
export const phone = (c: Person) => {
  const m = c.mobile_number ?? "";
  const local = m.length === 10 ? `${m.slice(0, 5)} ${m.slice(5)}` : m;
  return c.mobile_country_code ? `+${c.mobile_country_code} ${local}` : local;
};

/** "NO 06 yashod nagar, banglore, karnataka 560091, IN" */
export const address = (c: Person) => {
  const a = c.address;
  const statePostal = [a.state_province, a.postal_code].filter(Boolean).join(" ");
  return [a.street_address, a.city, statePostal, a.country_code].filter(Boolean).join(", ");
};

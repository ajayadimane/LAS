import { useState, type ChangeEvent, type FormEvent } from "react";
import { useUpdateAddress, useUpdateEmail, type AddressInput, type BdCustomer } from "@/api/bd";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field } from "@/components/ui/Field";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { fullName, phone } from "@/lib/customer";

/**
 * Edit a customer's email and address. Each is a separate vendor update, so
 * only what changed is sent, and each succeeds or fails on its own. Name and
 * mobile number have no update API yet (the mobile is also the person id).
 * The email change is on the person record only; the login keeps its email.
 */

type Form = { email_address: string } & AddressInput;

const fromCustomer = (c: BdCustomer): Form => ({
  email_address: c.email_address ?? "",
  street_address: c.address.street_address ?? "",
  city: c.address.city ?? "",
  state_province: c.address.state_province ?? "",
  postal_code: c.address.postal_code ?? "",
  country_code: c.address.country_code ?? "IN",
});

/** Same rules as UpdateEmailRequest / UpdateAddressRequest in app/schemas/bd.py. */
function validate(f: Form): Partial<Record<keyof Form, string>> {
  const e: Partial<Record<keyof Form, string>> = {};
  const len = (v: string) => v.trim().length;
  if (!/^\S+@\S+\.\S+$/.test(f.email_address.trim())) e.email_address = "Enter a valid email address.";
  else if (len(f.email_address) > 100) e.email_address = "Use at most 100 characters.";
  if (len(f.street_address) < 1 || len(f.street_address) > 100) e.street_address = "Enter the street address (up to 100 characters).";
  if (len(f.city) < 1 || len(f.city) > 100) e.city = "Enter the city.";
  if (len(f.state_province) < 2 || len(f.state_province) > 100) e.state_province = "Enter the state.";
  if (len(f.postal_code) < 5 || len(f.postal_code) > 12) e.postal_code = "Enter a valid PIN code.";
  if (!/^[A-Za-z]{2}$/.test(f.country_code.trim())) e.country_code = "Use a 2-letter country code, e.g. IN.";
  return e;
}

const ADDRESS_KEYS = ["street_address", "city", "state_province", "postal_code", "country_code"] as const;

export function EditDetailsDialog({ open, onClose, bdId, customer }: {
  open: boolean;
  onClose: () => void;
  bdId: string;
  customer: BdCustomer;
}) {
  const personId = customer.person_id ?? "";
  const toast = useToast();
  const email = useUpdateEmail(bdId, personId);
  const address = useUpdateAddress(bdId, personId);
  // What the vendor has; a part saved here becomes the new baseline, so a retry resends only what failed.
  const [initial, setInitial] = useState(() => fromCustomer(customer));
  const [form, setForm] = useState(initial);
  const [submitted, setSubmitted] = useState(false);
  const [failed, setFailed] = useState<string[]>([]);

  const errors = validate(form);
  const show = (k: keyof Form) => submitted && errors[k];
  const set = (k: keyof Form) => (e: ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const trimmed = (f: Form) => Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim()])) as Form;

  const t = trimmed(form);
  const emailChanged = t.email_address !== initial.email_address.trim();
  const addressChanged = ADDRESS_KEYS.some((k) => t[k] !== initial[k].trim());
  const busy = email.isPending || address.isPending;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length || busy) return;
    if (!emailChanged && !addressChanged) return onClose();

    const done: string[] = [];
    const errs: string[] = [];
    if (emailChanged) {
      try {
        await email.mutateAsync({ email_address: t.email_address });
        setInitial((i) => ({ ...i, email_address: t.email_address }));
        done.push("email");
      } catch (err) {
        errs.push(`Email: ${err instanceof Error ? err.message : "not saved"}`);
      }
    }
    if (addressChanged) {
      try {
        const { email_address: _, ...addr } = t;
        await address.mutateAsync({ ...addr, country_code: addr.country_code.toUpperCase() });
        setInitial((i) => ({ ...i, ...addr }));
        done.push("address");
      } catch (err) {
        errs.push(`Address: ${err instanceof Error ? err.message : "not saved"}`);
      }
    }
    setFailed(errs);
    if (done.length) toast(`${fullName(customer)}: ${done.join(" and ")} updated`, "success");
    if (!errs.length) onClose();
  };

  return (
    <Dialog open={open} onClose={busy ? () => {} : onClose} title="Edit customer details" large>
      <form onSubmit={submit} noValidate className="space-y-5">
        <div className="grid grid-cols-1 gap-x-6 gap-y-4 rounded-xl bg-muted/60 p-4 text-sm md:grid-cols-2">
          <div>
            <p className="text-xs font-semibold text-muted-foreground">Full name</p>
            <p className="mt-1 font-medium">{fullName(customer)}</p>
          </div>
          <div>
            <p className="text-xs font-semibold text-muted-foreground">Mobile number</p>
            <p className="mt-1 font-medium">{phone(customer)}</p>
          </div>
          <p className="text-xs text-muted-foreground md:col-span-2">Name and mobile number can't be changed here yet.</p>
        </div>

        <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
          <Field label="Email ID" required htmlFor="ed-email" error={show("email_address")} className="md:col-span-2">
            <Input id="ed-email" type="email" className="h-12" autoComplete="off" value={form.email_address} onChange={set("email_address")} />
          </Field>
          <Field label="Street address" required htmlFor="ed-street" error={show("street_address")} className="md:col-span-2">
            <Input id="ed-street" className="h-12" value={form.street_address} onChange={set("street_address")} />
          </Field>
          <Field label="City" required htmlFor="ed-city" error={show("city")}>
            <Input id="ed-city" className="h-12" value={form.city} onChange={set("city")} />
          </Field>
          <Field label="State" required htmlFor="ed-state" error={show("state_province")}>
            <Input id="ed-state" className="h-12" value={form.state_province} onChange={set("state_province")} />
          </Field>
          <Field label="PIN code" required htmlFor="ed-pin" error={show("postal_code")}>
            <Input id="ed-pin" className="h-12" inputMode="numeric" value={form.postal_code} onChange={set("postal_code")} />
          </Field>
          <Field label="Country code" required htmlFor="ed-country" error={show("country_code")}>
            <Input id="ed-country" className="h-12" maxLength={2} value={form.country_code} onChange={set("country_code")} />
          </Field>
        </div>

        {failed.length > 0 && (
          <div className="rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {failed.map((m) => <p key={m}>{m}</p>)}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <Button type="button" variant="outline" className="rounded-xl" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" className="rounded-xl" disabled={busy || (!emailChanged && !addressChanged)}>
            {busy ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

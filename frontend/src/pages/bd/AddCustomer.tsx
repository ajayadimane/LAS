import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateCustomer, useCreateLogin, useOpenWallet, type CreateCustomerInput } from "@/api/bd";
import { ApiError } from "@/api/client";
import { useSession } from "@/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { useToast } from "@/components/ui/Toast";
import { copyText } from "@/lib/format";
import { paths } from "@/routes";
import { ServicesPayment } from "./ServicesPayment";

/**
 * Adding a customer is: register the person, open their wallet, create their
 * login (all in one middleware call). Services unlock once the wallet is open;
 * opting, payment and documents are in ServicesPayment.
 * The login's temporary password is shown once, here; it is never stored.
 */

type Form = Required<Omit<CreateCustomerInput, "country_code">>;

const EMPTY: Form = {
  first_name: "", last_name: "", mobile_number: "", email_address: "",
  street_address: "", city: "", state_province: "", postal_code: "",
};

const digits = (s: string) => s.replace(/\D/g, "");

/** Same rules as CreateCustomerRequest in app/schemas/bd.py. */
function validate(f: Form): Partial<Record<keyof Form, string>> {
  const e: Partial<Record<keyof Form, string>> = {};
  const len = (v: string) => v.trim().length;
  if (len(f.first_name) < 1) e.first_name = "Enter the first name.";
  else if (len(f.first_name) > 50) e.first_name = "Use at most 50 characters.";
  if (len(f.last_name) > 50) e.last_name = "Use at most 50 characters.";
  if (digits(f.mobile_number).length !== 10) e.mobile_number = "Enter a 10-digit mobile number.";
  if (!/^\S+@\S+\.\S+$/.test(f.email_address.trim())) e.email_address = "Enter a valid email address.";
  else if (len(f.email_address) > 100) e.email_address = "Use at most 100 characters.";
  if (len(f.street_address) < 1 || len(f.street_address) > 100) e.street_address = "Enter the street address (up to 100 characters).";
  if (len(f.city) < 1 || len(f.city) > 100) e.city = "Enter the city.";
  if (len(f.state_province) < 2 || len(f.state_province) > 100) e.state_province = "Enter the state.";
  if (len(f.postal_code) < 5 || len(f.postal_code) > 12) e.postal_code = "Enter a postal code of 5 to 12 characters.";
  return e;
}

export default function AddCustomer() {
  const navigate = useNavigate();
  const toast = useToast();
  const session = useSession()!; // RequireRole guarantees a BD session
  const create = useCreateCustomer(session.userId);
  const openWallet = useOpenWallet(session.userId);
  const createLogin = useCreateLogin(session.userId);

  const [form, setForm] = useState<Form>(EMPTY);
  const [submitted, setSubmitted] = useState(false);

  const errors = validate(form);
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const show = (k: keyof Form) => submitted && errors[k];

  // Registered: by this form, or (after a 409) an existing customer whose wallet we finished.
  const personId = create.data?.person_id ?? openWallet.data?.person_id ?? null;
  const walletReady = !!create.data?.wallet_opened || openWallet.isSuccess;
  const locked = personId !== null || create.isPending;
  const alreadyRegistered = create.error instanceof ApiError && create.error.status === 409;
  const name = [form.first_name.trim(), form.last_name.trim()].filter(Boolean).join(" ");

  const loginReady = !!create.data?.login_created || (createLogin.isSuccess && !createLogin.data.already_exists);
  const loginExisted = !!createLogin.data?.already_exists || create.data?.login_error?.status === 409;
  const password = createLogin.data?.temporary_password ?? create.data?.temporary_password ?? null;
  const mailResult = createLogin.data?.temporary_password ? createLogin.data : create.data;
  const emailed = mailResult?.credentials_emailed ? form.email_address.trim() : null;
  const emailError = mailResult?.credentials_email_error ?? null;
  const loginUserId = createLogin.data?.login_user_id ?? create.data?.login_user_id ?? personId;
  const loginFailed = personId !== null && !loginReady && !loginExisted && (!!create.data?.login_error || createLogin.isError);

  const onSave = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length || locked) return;
    create.mutate(
      {
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim() || undefined,
        mobile_number: digits(form.mobile_number),
        email_address: form.email_address.trim(),
        street_address: form.street_address.trim(),
        city: form.city.trim(),
        state_province: form.state_province.trim(),
        postal_code: form.postal_code.trim(),
      },
      {
        onSuccess: (res) => {
          const ok = res.wallet_opened && res.login_created;
          toast(ok ? `${name} saved, wallet opened and login created` : `${name} saved, but setup is not complete`,
            ok ? "success" : "default");
        },
      },
    );
  };

  const retryWallet = (id: string) =>
    openWallet.mutate(id, { onSuccess: () => toast("Wallet opened", "success") });

  const retryLogin = (id: string) =>
    createLogin.mutate(id, {
      onSuccess: (res) => toast(res.already_exists ? "This customer already has a login" : "Login created", "success"),
    });

  // An already-registered customer: finish whatever steps are missing.
  const finishSetup = (id: string) =>
    openWallet.mutate(id, { onSuccess: () => retryLogin(id) });


  return (
    <div className="min-h-full bg-background px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-[980px]">
        <button onClick={() => navigate(paths.bdDashboard)}
          className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground">
          <Icon name="left" /> Customers / Add new customer
        </button>
        <h1 className="mt-6 text-3xl font-extrabold tracking-tight">Add new customer</h1>

        {/* 1. Customer details */}
        <form onSubmit={onSave} noValidate className="mt-8 rounded-3xl border bg-card p-6 sm:p-7">
          <StepTitle n={1} done={personId !== null}>Customer details</StepTitle>

          <fieldset disabled={locked} className="mt-6 grid grid-cols-1 gap-5 disabled:opacity-70 md:grid-cols-2">
            <Field label="First name" required htmlFor="ac-first" error={show("first_name")}>
              <Input id="ac-first" className="h-12" placeholder="Enter first name" autoComplete="off"
                value={form.first_name} onChange={set("first_name")} />
            </Field>
            <Field label="Last name" htmlFor="ac-last" error={show("last_name")}>
              <Input id="ac-last" className="h-12" placeholder="Enter last name" autoComplete="off"
                value={form.last_name} onChange={set("last_name")} />
            </Field>
            <Field label="Mobile number" required htmlFor="ac-mob" error={show("mobile_number")}>
              <div className="field-wrap flex h-12 items-center rounded-xl border border-input bg-card">
                <span className="pl-3 pr-2 text-sm text-muted-foreground">+91</span>
                <input id="ac-mob" inputMode="numeric" placeholder="98765 43210" autoComplete="off"
                  className="h-full w-full rounded-r-xl bg-transparent pr-3 text-sm outline-none placeholder:text-muted-foreground"
                  value={form.mobile_number}
                  onChange={(e) => setForm((f) => ({ ...f, mobile_number: e.target.value.replace(/[^\d ]/g, "").slice(0, 11) }))} />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">Also becomes the customer's ID.</p>
            </Field>
            <Field label="Email ID" required htmlFor="ac-email" error={show("email_address")}>
              <Input id="ac-email" type="email" className="h-12" placeholder="name@example.com" autoComplete="off"
                value={form.email_address} onChange={set("email_address")} />
            </Field>
            <Field label="Street address" required htmlFor="ac-street" error={show("street_address")} className="md:col-span-2">
              <Input id="ac-street" className="h-12" placeholder="House no., street, area"
                value={form.street_address} onChange={set("street_address")} />
            </Field>
            <Field label="City" required htmlFor="ac-city" error={show("city")}>
              <Input id="ac-city" className="h-12" placeholder="Bengaluru" value={form.city} onChange={set("city")} />
            </Field>
            <Field label="State" required htmlFor="ac-state" error={show("state_province")}>
              <Input id="ac-state" className="h-12" placeholder="Karnataka" value={form.state_province} onChange={set("state_province")} />
            </Field>
            <Field label="Postal code" required htmlFor="ac-pin" error={show("postal_code")}>
              <Input id="ac-pin" inputMode="numeric" className="h-12" placeholder="560001"
                value={form.postal_code} onChange={set("postal_code")} />
            </Field>
            <Field label="Country" htmlFor="ac-country">
              <Input id="ac-country" className="h-12" value="India (IN)" disabled />
            </Field>
          </fieldset>

          {create.isError && (
            <Notice tone="error">
              <p>{create.error.message}</p>
              {alreadyRegistered && !openWallet.isSuccess && (
                <div className="mt-2">
                  <p className="text-xs">
                    If you added this customer before and their wallet or login was not set up, you can finish that now.
                  </p>
                  <Button size="sm" variant="outline" className="mt-2 rounded-lg" disabled={openWallet.isPending}
                    onClick={() => finishSetup(digits(form.mobile_number))}>
                    {openWallet.isPending ? "Opening wallet…" : "Finish setup for this customer"}
                  </Button>
                  {openWallet.isError && <p className="mt-2 text-xs">{openWallet.error.message}</p>}
                </div>
              )}
            </Notice>
          )}

          {personId && walletReady && (
            <Notice tone="success">
              {create.data ? `${name} is registered and their wallet is open.` : "Wallet opened for the existing customer."}
              {" "}Choose services below.
            </Notice>
          )}

          {create.data && !walletReady && (
            <Notice tone="warning">
              <p>{name} is registered, but their wallet could not be opened: {create.data.wallet_error?.message}</p>
              <Button size="sm" className="mt-2 rounded-lg" disabled={openWallet.isPending} onClick={() => retryWallet(create.data.person_id)}>
                {openWallet.isPending ? "Opening wallet…" : "Retry opening wallet"}
              </Button>
              {openWallet.isError && <p className="mt-2 text-xs">{openWallet.error.message}</p>}
            </Notice>
          )}

          {password && loginUserId && <Credentials userId={loginUserId} password={password} emailedTo={emailed} emailError={emailError} />}

          {personId && loginExisted && !password && (
            <Notice tone="success">This customer already has a login. Their password is unchanged.</Notice>
          )}

          {loginFailed && (
            <Notice tone="warning">
              <p>
                {name || "The customer"} is registered, but their login could not be created:{" "}
                {createLogin.error?.message ?? create.data?.login_error?.message}
              </p>
              <Button size="sm" className="mt-2 rounded-lg" disabled={createLogin.isPending} onClick={() => retryLogin(personId)}>
                {createLogin.isPending ? "Creating login…" : "Retry creating login"}
              </Button>
            </Notice>
          )}

          {personId === null && (
            <div className="mt-6 flex flex-wrap items-center justify-end gap-3">
              {submitted && Object.keys(errors).length > 0 && (
                <p className="mr-auto text-xs text-destructive">Fix the highlighted fields to continue.</p>
              )}
              <Button variant="outline" className="h-11 rounded-xl" onClick={() => navigate(paths.bdDashboard)}>Cancel</Button>
              <Button type="submit" className="h-11 rounded-xl" disabled={create.isPending}>
                {create.isPending ? "Saving customer…" : <>Save customer <Icon name="right" /></>}
              </Button>
            </div>
          )}
        </form>

        <ServicesPayment bdId={session.userId} personId={walletReady ? personId : null} step={2} />

        {personId && (
          <div className="mt-6 flex flex-wrap justify-end gap-3">
            <Button variant="outline" className="h-11 rounded-xl" onClick={() => navigate(paths.bdDashboard)}>Back to customers</Button>
            <Button className="h-11 rounded-xl" onClick={() => navigate(paths.bdCustomer(personId))}>View customer <Icon name="right" /></Button>
          </div>
        )}
      </div>
    </div>
  );
}

/** The customer's sign-in details. The password exists only in this response. */
function Credentials({ userId, password, emailedTo, emailError }: {
  userId: string;
  password: string;
  /** Where the details were emailed; null = not emailed. */
  emailedTo: string | null;
  emailError: string | null;
}) {
  const toast = useToast();
  const copy = async () =>
    toast((await copyText(`User ID: ${userId}\nPassword: ${password}`)) ? "Login details copied" : "Could not copy", "default");
  return (
    <div role="status" className="mt-6 rounded-xl border-2 border-dashed border-primary/40 bg-accent/40 px-4 py-4 text-sm">
      <p className="font-bold">Customer login created</p>
      {emailedTo ? (
        <p className="mt-1 flex items-center gap-1.5 text-success-foreground">
          <Icon name="check" className="h-3.5 w-3.5" /> Login details emailed to {emailedTo}
        </p>
      ) : (
        <p className="mt-1 text-warning-foreground">{emailError ?? "The login details were not emailed."}</p>
      )}
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
        <dt className="text-muted-foreground">User ID</dt>
        <dd className="font-mono font-semibold">{userId}</dd>
        <dt className="text-muted-foreground">Password</dt>
        <dd className="break-all font-mono font-semibold">{password}</dd>
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" className="rounded-lg" onClick={copy}>Copy login details</Button>
        <p className="text-xs text-muted-foreground">
          {emailedTo ? "Keep these in case the email doesn't arrive." : "Share these with the customer now."} The
          password is shown only once and is not stored anywhere.
        </p>
      </div>
    </div>
  );
}

function StepTitle({ n, done, children }: { n: number; done?: boolean; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
        {done ? <Icon name="check" className="h-4 w-4" /> : n}
      </span>
      <h2 className="text-lg font-bold">{children}</h2>
    </div>
  );
}

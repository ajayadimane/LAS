import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateCustomer, useCreateLogin, useOpenWallet, type CreateCustomerInput } from "@/api/bd";
import { ApiError } from "@/api/client";
import { proves, useOtpSettings, type OtpProof } from "@/api/otp";
import { useSession } from "@/auth/AuthContext";
import { useCustomerServices } from "@/api/services";
import { AnalysisReportCard } from "@/components/AnalysisReport";
import { OtpVerify } from "@/components/OtpVerify";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { SectionCard } from "@/components/ui/SectionCard";
import { StepBadge, Stepper } from "@/components/ui/Stepper";
import { useToast } from "@/components/ui/Toast";
import { copyText } from "@/lib/format";
import { paths } from "@/routes";
import { ServicesPayment } from "./ServicesPayment";

/**
 * Adding a customer is: register the person, open their wallet, create their
 * login (all in one middleware call). Services unlock once the wallet is open;
 * opting, payment and documents are in ServicesPayment.
 *
 * Shown as a four-step wizard: details, services & payment, uploads, analysis.
 * The step is page state, not in the URL: the temporary password lives only
 * in this page's memory, and a reload of a later step would lose it.
 * The login's temporary password is shown once, here; it is never stored.
 * The mobile number and email are each verified by OTP before saving.
 */

type Form = Required<Omit<CreateCustomerInput, "country_code" | "mobile_otp_token" | "email_otp_token">>;

const EMPTY: Form = {
  first_name: "", last_name: "", mobile_number: "", email_address: "",
  street_address: "", city: "", state_province: "", postal_code: "",
};

const STEPS = ["Customer details", "Services & docs", "Uploads", "Analysis"];

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
  const [proofs, setProofs] = useState<{ mobile?: OtpProof; email?: OtpProof }>({});
  // A mobile the OTP step found already registered (409 before any OTP is sent).
  const [registeredMobile, setRegisteredMobile] = useState<string | null>(null);
  const otpRequired = useOtpSettings().data?.required ?? true;

  const fieldErrors = validate(form);
  const mobile = digits(form.mobile_number);
  // Both proofs are for this registration, i.e. this mobile: change it and both start over.
  const mobileVerified = proves(proofs.mobile, "mobile", form.mobile_number, mobile);
  const emailVerified = proves(proofs.email, "email", form.email_address, mobile);
  const errors = { ...fieldErrors };
  if (otpRequired && !errors.mobile_number && !mobileVerified) errors.mobile_number = "Verify the mobile number with an OTP.";
  if (otpRequired && !errors.email_address && !emailVerified) errors.email_address = "Verify the email with an OTP.";
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const show = (k: keyof Form) => submitted && errors[k];

  // Registered: by this form, or (after a 409) an existing customer whose wallet we finished.
  const personId = create.data?.person_id ?? openWallet.data?.person_id ?? null;
  const walletReady = !!create.data?.wallet_opened || openWallet.isSuccess;
  const locked = personId !== null || create.isPending;
  const alreadyRegistered =
    (create.error instanceof ApiError && create.error.status === 409) || (registeredMobile !== null && registeredMobile === mobile);
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
        mobile_otp_token: mobileVerified ? proofs.mobile!.token : undefined,
        email_otp_token: emailVerified ? proofs.email!.token : undefined,
      },
      {
        onSuccess: (res) => {
          const ok = res.wallet_opened && res.login_created;
          toast(ok ? `${name} saved, service fee account opened and login created` : `${name} saved, but setup is not complete`,
            ok ? "success" : "default");
        },
      },
    );
  };

  const retryWallet = (id: string) =>
    openWallet.mutate(id, { onSuccess: () => toast("Service fee account opened", "success") });

  const retryLogin = (id: string) =>
    createLogin.mutate(id, {
      onSuccess: (res) => toast(res.already_exists ? "This customer already has a login" : "Login created", "success"),
    });

  const services = useCustomerServices(session.userId, walletReady ? personId : null);
  const paid = services.data?.services.filter((sv) => sv.opted) ?? [];
  const withDocs = paid.filter((sv) => sv.document);
  // How far the wizard may go: services need the wallet, uploads a paid service, analysis a document.
  const reachable = !walletReady ? 1 : paid.length === 0 ? 2 : withDocs.length === 0 ? 3 : 4;
  const [step, setStep] = useState(1);
  const go = (n: number) => { setStep(n); window.scrollTo({ top: 0, behavior: "smooth" }); };

  // An already-registered customer: finish whatever steps are missing.
  const finishSetup = (id: string) =>
    openWallet.mutate(id, { onSuccess: () => retryLogin(id) });


  return (
    <div className="min-h-full bg-background px-4 py-10 sm:px-6">
      <div className="mx-auto w-full max-w-[980px]">
        <PageHeader
          title="Add New Customer"
          subtitle="Complete the customer onboarding process."
          onBack={() => navigate(paths.bdDashboard)}
          backLabel="Back to customers"
          actions={<StepBadge current={step} total={STEPS.length} />}
        />
        <Stepper className="mt-6" steps={STEPS} current={step} reachable={reachable} onSelect={go} />

        {/* 1. Customer details */}
        {step === 1 && (
        <form onSubmit={onSave} noValidate className="mt-6">
          <SectionCard step={1} title="Customer details"
            subtitle="Provide the primary borrower's personal identification and residential address.">
          <fieldset disabled={locked} className="grid grid-cols-1 gap-5 disabled:opacity-70 md:grid-cols-2">
            <Field label="First name" required htmlFor="ac-first" error={show("first_name")}>
              <Input id="ac-first" icon="user" className="h-12 bg-surface" placeholder="Enter first name" autoComplete="off"
                value={form.first_name} onChange={set("first_name")} />
            </Field>
            <Field label="Last name" htmlFor="ac-last" error={show("last_name")}>
              <Input id="ac-last" icon="user" className="h-12 bg-surface" placeholder="Enter last name" autoComplete="off"
                value={form.last_name} onChange={set("last_name")} />
            </Field>
            <Field label="Mobile number" required htmlFor="ac-mob" error={show("mobile_number")}>
              <div className="field-wrap flex h-12 items-center rounded-xl border border-input bg-surface">
                <span className="flex items-center gap-2 pl-3.5 pr-2 text-sm text-muted-foreground"><Icon name="phone" /> +91</span>
                <input id="ac-mob" inputMode="numeric" placeholder="98765 43210" autoComplete="off"
                  className="h-full w-full rounded-r-xl bg-transparent pr-3 text-sm outline-none placeholder:text-muted-foreground"
                  value={form.mobile_number}
                  onChange={(e) => setForm((f) => ({ ...f, mobile_number: e.target.value.replace(/[^\d ]/g, "").slice(0, 11) }))} />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">Also becomes the customer's ID.</p>
              {otpRequired && (
                <OtpVerify channel="mobile" purpose="register" value={form.mobile_number} personId={mobile}
                  ready={!fieldErrors.mobile_number} verified={mobileVerified}
                  onVerified={(proof) => setProofs((p) => ({ ...p, mobile: proof }))}
                  onError={(err) => err.code === "CUSTOMER_ALREADY_REGISTERED" && setRegisteredMobile(mobile)} />
              )}
            </Field>
            <Field label="Email ID" required htmlFor="ac-email" error={show("email_address")}>
              <Input id="ac-email" type="email" icon="mail" className="h-12 bg-surface" placeholder="name@example.com" autoComplete="off"
                value={form.email_address} onChange={set("email_address")} />
              {otpRequired && (
                <OtpVerify channel="email" purpose="register" value={form.email_address} personId={mobile}
                  ready={!fieldErrors.email_address && !fieldErrors.mobile_number} verified={emailVerified}
                  onVerified={(proof) => setProofs((p) => ({ ...p, email: proof }))} />
              )}
              {otpRequired && !fieldErrors.email_address && fieldErrors.mobile_number && (
                <p className="mt-1.5 text-xs text-muted-foreground">Enter the mobile number first.</p>
              )}
            </Field>
            <Field label="Street address" required htmlFor="ac-street" error={show("street_address")} className="md:col-span-2">
              <Input id="ac-street" icon="mappin" className="h-12 bg-surface" placeholder="House no., street, area"
                value={form.street_address} onChange={set("street_address")} />
            </Field>
            <Field label="City" required htmlFor="ac-city" error={show("city")}>
              <Input id="ac-city" icon="mappin" className="h-12 bg-surface" placeholder="Bengaluru" value={form.city} onChange={set("city")} />
            </Field>
            <Field label="State" required htmlFor="ac-state" error={show("state_province")}>
              <Input id="ac-state" icon="mappin" className="h-12 bg-surface" placeholder="Karnataka" value={form.state_province} onChange={set("state_province")} />
            </Field>
            <Field label="Postal code" required htmlFor="ac-pin" error={show("postal_code")}>
              <Input id="ac-pin" inputMode="numeric" icon="hash" className="h-12 bg-surface" placeholder="560001"
                value={form.postal_code} onChange={set("postal_code")} />
            </Field>
            <Field label="Country" htmlFor="ac-country">
              <Input id="ac-country" icon="globe" className="h-12 bg-muted" value="India (IN)" disabled />
            </Field>
          </fieldset>

          {(create.isError || alreadyRegistered) && (
            <Notice tone="error">
              <p>{create.error?.message ?? "A customer with this mobile number is already registered."}</p>
              {alreadyRegistered && !openWallet.isSuccess && (
                <div className="mt-2">
                  <p className="text-xs">
                    If you added this customer before and their service fee account or login was not set up, you can finish that now.
                  </p>
                  <Button size="sm" variant="outline" className="mt-2 rounded-lg" disabled={openWallet.isPending}
                    onClick={() => finishSetup(digits(form.mobile_number))}>
                    {openWallet.isPending ? "Opening service fee account…" : "Finish setup for this customer"}
                  </Button>
                  {openWallet.isError && <p className="mt-2 text-xs">{openWallet.error.message}</p>}
                </div>
              )}
            </Notice>
          )}

          {personId && walletReady && (
            <Notice tone="success">
              {create.data ? `${name} is registered and their service fee account is open.` : "Service fee account opened for the existing customer."}
              {" "}Continue to choose services.
            </Notice>
          )}

          {create.data && !walletReady && (
            <Notice tone="warning">
              <p>{name} is registered, but their service fee account could not be opened: {create.data.wallet_error?.message}</p>
              <Button size="sm" className="mt-2 rounded-lg" disabled={openWallet.isPending} onClick={() => retryWallet(create.data.person_id)}>
                {openWallet.isPending ? "Opening service fee account…" : "Retry opening service fee account"}
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

          <div className="mt-7 flex flex-wrap items-center justify-end gap-3 border-t pt-6">
            {personId === null ? (
              <>
                {submitted && Object.keys(errors).length > 0 && (
                  <p className="mr-auto text-xs text-destructive">Fix the highlighted fields to continue.</p>
                )}
                <Button variant="outline" className="h-11 rounded-xl px-6" onClick={() => navigate(paths.bdDashboard)}>Cancel</Button>
                <Button type="submit" size="lg" disabled={create.isPending}>
                  {create.isPending ? "Saving customer…" : <>Save customer <Icon name="right" /></>}
                </Button>
              </>
            ) : (
              <Button size="lg" disabled={!walletReady} onClick={() => go(2)}>
                Continue to services <Icon name="right" />
              </Button>
            )}
          </div>
          </SectionCard>
        </form>
        )}

        {step === 2 && (
          <div className="mt-6">
            <ServicesPayment bdId={session.userId} personId={walletReady ? personId : null} step={2}
              onGoToUploads={() => go(3)} />
          </div>
        )}

        {step === 3 && (
          <div className="mt-6">
            <ServicesPayment bdId={session.userId} personId={walletReady ? personId : null} step={3} view="uploads" />
          </div>
        )}

        {step === 4 && personId && (
          <div className="mt-6 space-y-5">
            {withDocs.length === 0 ? (
              <SectionCard step={4} title="Analysis" subtitle="Upload a document to start its analysis." />
            ) : (
              withDocs.map((sv) => <AnalysisReportCard key={sv.code} bdId={session.userId} personId={personId} service={sv} />)
            )}
          </div>
        )}

        {personId && (
          <div className="mt-6 flex flex-wrap justify-end gap-3">
            {step > 1 && (
              <Button variant="outline" className="h-11 rounded-xl" onClick={() => go(step - 1)}>
                <Icon name="left" /> Back
              </Button>
            )}
            {step >= 2 && step < STEPS.length && (
              <Button variant="outline-primary" className="h-11 rounded-xl" disabled={reachable <= step} onClick={() => go(step + 1)}>
                Next: {STEPS[step]} <Icon name="right" />
              </Button>
            )}
            <Button className="h-11 rounded-xl" onClick={() => navigate(paths.bdCustomer(personId))}>View customer <Icon name="right" /></Button>
          </div>
        )}

        <p className="mt-8 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <Icon name="lock" className="h-3.5 w-3.5 text-primary" /> 256-bit encrypted bank-grade data transmission &amp; LAS regulatory compliant
        </p>
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

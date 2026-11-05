import type { CatalogService, ServiceDetails, ServiceDetailValue } from "@/api/services";
import { Input } from "@/components/ui/Input";

/**
 * What a service asks for when it is chosen (CatalogService.details), e.g.
 * DPR Analysis: bank name and project name. The middleware refuses a payment
 * or request without them, so the forms block until they are filled in.
 */

const clean = (v: string | undefined) => (v ?? "").replace(/\s+/g, " ").trim();
/** "NA" is how the vendor says "not set", so it is no answer. */
const given = (v: string | undefined) => clean(v) !== "" && clean(v).toUpperCase() !== "NA";

/** Every detail of every chosen service is filled in. */
export const detailsComplete = (services: CatalogService[], values: ServiceDetails) =>
  services.every((s) => s.details.every((d) => given(values[s.code]?.[d.key])));

/** The request's `details`: only chosen services that ask for any, trimmed. */
export const detailsPayload = (services: CatalogService[], values: ServiceDetails): ServiceDetails =>
  Object.fromEntries(
    services
      .filter((s) => s.details.length)
      .map((s) => [s.code, Object.fromEntries(s.details.map((d) => [d.key, clean(values[s.code]?.[d.key])]))]),
  );

export function ServiceDetailsFields({ service, values, onChange, disabled }: {
  service: CatalogService;
  values: ServiceDetails;
  onChange: (next: ServiceDetails) => void;
  disabled?: boolean;
}) {
  if (!service.details.length) return null;
  const mine = values[service.code] ?? {};
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {service.details.map((d) => {
        const id = `detail-${service.code}-${d.key}`;
        return (
          <div key={d.key}>
            <label htmlFor={id} className="text-xs font-semibold">{d.label} *</label>
            <Input id={id} className="mt-1 h-10" maxLength={d.max_length} disabled={disabled} autoComplete="off"
              placeholder={`Enter ${d.label.toLowerCase()}`} value={mine[d.key] ?? ""}
              onChange={(e) => onChange({ ...values, [service.code]: { ...mine, [d.key]: e.target.value } })} />
          </div>
        );
      })}
    </div>
  );
}

/** "Bank name: SBI · Project name: Dairy unit"; nothing when none is given. */
export function DetailsSummary({ details, className }: { details: ServiceDetailValue[] | undefined; className?: string }) {
  const shown = (details ?? []).filter((d) => d.value);
  if (!shown.length) return null;
  return (
    <p className={className ?? "mt-0.5 text-xs text-muted-foreground"}>
      {shown.map((d, i) => (
        <span key={d.key}>
          {i > 0 && " · "}
          {d.label}: <span className="font-medium text-foreground">{d.value}</span>
        </span>
      ))}
    </p>
  );
}

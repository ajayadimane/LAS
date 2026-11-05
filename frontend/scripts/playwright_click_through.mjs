// Click-through verification for Section 17's report status card, against
// the fake-vendor server (scripts/playwright_server.py) and a Vite dev
// server proxying to it. No real vendor, no real Gemini, no real money --
// see playwright_server.py's module docstring.
//
// Usage (from frontend/, where the `playwright` devDependency is installed):
//   node scripts/playwright_click_through.mjs <app-base-url>
//
// Writes one screenshot per scenario to ../docs/review/frontend/ (repo root).

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const OUT_DIR = join(REPO_ROOT, "docs", "review", "frontend");
mkdirSync(OUT_DIR, { recursive: true });

const BASE_URL = process.argv[2] ?? "http://127.0.0.1:5181";

const BD_ID = "97315";
const PID_PAID = "9100000001";
const PID_UNPAID = "9100000002";
const PID_FAILED = "9100000003";

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj))
    .toString("base64")
    .replace(/=+$/, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

function jwt(claims) {
  return `${b64url({ alg: "RS256", typ: "JWT" })}.${b64url(claims)}.c2ln`;
}

const bdToken = jwt({ preferred_username: BD_ID, groupId: ["LAS.admin"] });
const custToken = (id) => jwt({ preferred_username: id, groupId: ["LAS.enduser"] });

function logConsole(page, label) {
  page.on("console", (msg) => console.log(`[${label}] console:`, msg.text()));
  page.on("pageerror", (err) => console.log(`[${label}] pageerror:`, err.message));
  page.on("requestfailed", (req) =>
    console.log(`[${label}] requestfailed:`, req.url(), req.failure()?.errorText),
  );
  page.on("response", (res) => {
    if (res.status() >= 400) console.log(`[${label}] http ${res.status()}:`, res.url());
  });
}

async function setSession(page, { token, userId, role }) {
  await page.addInitScript(
    ({ token, userId, role }) => {
      localStorage.setItem(
        "las.session",
        JSON.stringify({ token, userId, role, expiresAt: null }),
      );
    },
    { token, userId, role },
  );
}

async function shot(page, name) {
  const path = join(OUT_DIR, `${name}.png`);
  await page.screenshot({ path, fullPage: true });
  console.log(`saved ${path}`);
}

async function main() {
  const browser = await chromium.launch();

  // 1. Paid customer: dashboard shows the service row, "View report" opens
  // the Section 17 status card with readiness + counts + financial overview.
  {
    const page = await browser.newContext().then((c) => c.newPage());
    await setSession(page, { token: custToken(PID_PAID), userId: PID_PAID, role: "customer" });
    await page.goto(`${BASE_URL}/customer`);
    await page.getByRole("button", { name: /view report/i }).first().click();
    await page.waitForSelector("text=Ready for submission review", { timeout: 15000 });
    await shot(page, "01-customer-paid-report-summary");
    await page.close();
  }

  // 2. Unpaid customer: the dashboard's own BD view shows the payment-locked
  // state (PAYMENT_NOT_VERIFIED), not a 404-as-if-nothing-happened.
  {
    const page = await browser.newContext().then((c) => c.newPage());
    await setSession(page, { token: bdToken, userId: BD_ID, role: "bd" });
    await page.goto(`${BASE_URL}/bd/customers/${PID_UNPAID}`);
    try {
      await page.waitForSelector("text=/is not available yet|PAYMENT_NOT_VERIFIED/i", {
        timeout: 15000,
      });
    } catch (e) {
      console.error("scenario 2: locked text not found, screenshotting anyway:", e.message);
    }
    await shot(page, "02-unpaid-customer-locked");
    await page.close();
  }

  // 3. BD view of the paid customer: same Section 17 card on the BD side.
  {
    const page = await browser.newContext().then((c) => c.newPage());
    logConsole(page, "scenario3");
    await setSession(page, { token: bdToken, userId: BD_ID, role: "bd" });
    await page.goto(`${BASE_URL}/bd/customers/${PID_PAID}`);
    try {
      await page.waitForSelector("text=Ready for submission review", { timeout: 15000 });
    } catch (e) {
      console.error("scenario 3: readiness text not found, screenshotting anyway:", e.message);
    }
    await shot(page, "03-bd-view-paid-customer");
    await page.close();
  }

  // 4. Failed PDF render: the status card still shows readiness/findings,
  // but "View / download PDF" surfaces the render failure explicitly.
  {
    const page = await browser.newContext().then((c) => c.newPage());
    await setSession(page, { token: bdToken, userId: BD_ID, role: "bd" });
    await page.goto(`${BASE_URL}/bd/customers/${PID_FAILED}`);
    try {
      await page.waitForSelector("text=Ready for submission review", { timeout: 15000 });
      await page.getByRole("button", { name: /view \/ download pdf/i }).click();
      await page.waitForSelector("text=/not available|could not/i", { timeout: 15000 });
    } catch (e) {
      console.error("scenario 4: expected text not found, screenshotting anyway:", e.message);
    }
    await shot(page, "04-failed-pdf-render");
    await page.close();
  }

  // 5. An older version: the paid customer has versions [1, 2]; fetch v1's
  // report directly (the dashboard has no version picker UI yet) to prove
  // the versioned store serves it distinctly, with the same bearer auth the
  // app itself uses (no vendor login needed -- see setSession()).
  {
    const page = await browser.newContext().then((c) => c.newPage());
    await page.setExtraHTTPHeaders({ Authorization: `Bearer ${bdToken}` });
    await page.goto(
      `${BASE_URL}/api/v1/bd/customers/${PID_PAID}/services/GEO_CHECK/report?version=1&broker_id=${BD_ID}`,
    );
    await shot(page, "05-older-version-v1-report-json");
    await page.close();
  }

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

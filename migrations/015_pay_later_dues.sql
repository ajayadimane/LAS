-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-014.
--
-- Pay later (user, 2026-11-16): the pool pays at once with the BD as
-- guarantor, but the customer still owes that amount. Until the BD confirms
-- the customer has paid, the service's report stays locked for both the
-- customer and the BD, and its PDF is not written to the wallet.
--
-- Kept here rather than in a vendor custom field: the vendor never adds a
-- new field to an existing person, so a field could never be settled for
-- anyone registered before it. A Pay later service with NO row here counts
-- as due (fail closed).

CREATE TABLE IF NOT EXISTS pay_later_dues (
    person_id TEXT NOT NULL,
    service_code TEXT NOT NULL,
    -- Whole rupees this service owes: its fee, plus the joining fee on the
    -- first service of a first payment. NULL = unknown (recorded late).
    amount INT,
    transfer_txn TEXT,
    status TEXT NOT NULL DEFAULT 'DUE' CHECK (status IN ('DUE', 'SETTLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at TIMESTAMPTZ,
    -- The BD id who confirmed receipt.
    settled_by TEXT,
    PRIMARY KEY (person_id, service_code)
);

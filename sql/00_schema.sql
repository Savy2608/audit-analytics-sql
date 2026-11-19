-- Schema for one fictional company's accounts payable and general ledger, 2025.
-- All IDs are text with a table prefix (E, V, INV, PAY, JE) so a single
-- record_id is unique across the whole database. Rerunning this file drops
-- and recreates every table.

DROP TABLE IF EXISTS journal_entries, payments, invoices, vendors, employees CASCADE;

CREATE TABLE employees (
    employee_id         TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    department          TEXT NOT NULL,
    role                TEXT NOT NULL,          -- manager, ap_supervisor, ap_clerk, accountant, staff
    address             TEXT NOT NULL,
    bank_account        TEXT NOT NULL,
    approval_limit_usd  NUMERIC(14, 2) NOT NULL
);

CREATE TABLE vendors (
    vendor_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    address       TEXT NOT NULL,
    bank_account  TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    created_date  DATE NOT NULL,
    inactive_date DATE,                         -- set only when status = 'inactive'
    CHECK ((status = 'inactive') = (inactive_date IS NOT NULL))
);

CREATE TABLE invoices (
    invoice_id      TEXT PRIMARY KEY,
    vendor_id       TEXT NOT NULL REFERENCES vendors (vendor_id),
    invoice_number  TEXT NOT NULL,              -- number printed by the vendor
    invoice_date    DATE NOT NULL,
    amount_usd      NUMERIC(14, 2) NOT NULL CHECK (amount_usd > 0),
    created_by      TEXT NOT NULL REFERENCES employees (employee_id),
    approved_by     TEXT NOT NULL REFERENCES employees (employee_id),
    approved_date   DATE NOT NULL
);

CREATE TABLE payments (
    payment_id    TEXT PRIMARY KEY,
    invoice_id    TEXT NOT NULL REFERENCES invoices (invoice_id),
    vendor_id     TEXT NOT NULL REFERENCES vendors (vendor_id),
    payment_date  DATE NOT NULL,
    amount_usd    NUMERIC(14, 2) NOT NULL CHECK (amount_usd > 0)
);

CREATE TABLE journal_entries (
    entry_id        TEXT PRIMARY KEY,
    entry_datetime  TIMESTAMP NOT NULL,
    account         TEXT NOT NULL,
    amount_usd      NUMERIC(14, 2) NOT NULL CHECK (amount_usd > 0),
    source          TEXT NOT NULL CHECK (source IN ('manual', 'automated')),
    created_by      TEXT NOT NULL REFERENCES employees (employee_id),
    approved_by     TEXT NOT NULL REFERENCES employees (employee_id),
    description     TEXT NOT NULL
);

-- Indexes that support the audit checks
CREATE INDEX idx_invoices_vendor_date     ON invoices (vendor_id, invoice_date);
CREATE INDEX idx_payments_invoice         ON payments (invoice_id);
CREATE INDEX idx_payments_vendor_amount   ON payments (vendor_id, amount_usd, payment_date);
CREATE INDEX idx_journal_created_by       ON journal_entries (created_by);
CREATE INDEX idx_journal_entry_datetime   ON journal_entries (entry_datetime);

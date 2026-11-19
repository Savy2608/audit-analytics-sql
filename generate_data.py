"""Generate a synthetic accounts-payable dataset with planted audit anomalies.

One fictional company, calendar year 2025. Everything is driven by a single
random seed, so the same seed always produces byte-identical CSV files.

Outputs (in --out-dir, default ./data):
    employees.csv, vendors.csv, invoices.csv, payments.csv, journal_entries.csv
    ground_truth.csv  - every planted anomaly (check_name, record_id)
    near_misses.csv   - legitimate records that look suspicious (check_name, record_id)

Usage:
    python generate_data.py                 # defaults: seed 42, spec sizes
    python generate_data.py --seed 7 --invoices 2000
"""

import argparse
import csv
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from faker import Faker

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)
APPROVAL_THRESHOLD_USD = 5000

# Planted anomalies per check (fixed, independent of table sizes)
N_DUPLICATE_PAYMENTS = 25
N_SPLIT_GROUPS = 15
N_SELF_APPROVED_INVOICES = 20
N_SELF_APPROVED_ENTRIES = 20
N_WEEKEND_ROUND_ENTRIES = 30
N_FICTITIOUS_VENDORS = 5
N_INACTIVE_VENDOR_PAYMENTS = 12
N_BENFORD_ENTRIES = 300

# Near-misses: legitimate records that resemble an anomaly
N_RECURRING_VENDORS = 3            # monthly invoices, same amount, ~30 days apart
N_SPLIT_NEAR_MISS_GROUPS = 10      # same vendor/clerk/week, total under 5,000
N_AUTOMATED_WEEKEND_ENTRIES = 24   # month-end batch on a weekend night, non-round
N_ROUND_BUSINESS_HOUR_ENTRIES = 20 # round accruals, weekday business hours
N_INACTIVE_NEAR_MISSES = 8         # paid a few days before the vendor was closed

ACCOUNTS = {
    "5000 Cost of Goods Sold": "Inventory cost allocation",
    "6100 Travel": "Travel expense reclass",
    "6200 Office Supplies": "Office supplies expense",
    "6300 Professional Fees": "Consulting fee accrual",
    "6400 Rent": "Rent expense allocation",
    "6500 Utilities": "Utilities accrual",
    "6600 Software Subscriptions": "Software subscription expense",
    "7100 Marketing": "Marketing campaign expense",
    "2100 Accrued Liabilities": "Accrued liabilities adjustment",
    "1200 Accounts Receivable": "Receivable correction",
}

MANAGER_DEPARTMENTS = ["Finance", "Operations", "Sales", "IT", "HR", "Procurement"]
STAFF_DEPARTMENTS = ["Operations", "Sales", "IT", "HR", "Procurement", "Marketing"]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def is_weekend(d):
    return d.weekday() >= 5


def next_weekday(d):
    """Move a date forward to Monday if it falls on a weekend."""
    while is_weekend(d):
        d += timedelta(days=1)
    return d


def previous_weekday(d):
    while is_weekend(d):
        d -= timedelta(days=1)
    return d


def random_date(rng, start, end):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def random_weekday(rng, start, end):
    while True:
        d = random_date(rng, start, end)
        if not is_weekend(d):
            return d


def lognormal_amount(rng, median, sigma, low, high):
    """Log-normal amounts give first digits close to Benford's Law."""
    value = rng.lognormvariate(math.log(median), sigma)
    return round(min(max(value, low), high), 2)


def fake_bank_account(rng):
    """Obviously fake account string, not tied to any real bank format."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "FAKEBANK-" + "".join(rng.choices(alphabet, k=10))


def money(value):
    return f"{value:.2f}"


# --------------------------------------------------------------------------- #
# Generator
# --------------------------------------------------------------------------- #
class DatasetGenerator:
    def __init__(self, seed, n_employees, n_vendors, n_invoices, n_entries):
        self.rng = random.Random(seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.n_employees = n_employees
        self.n_vendors = n_vendors
        self.n_invoices = n_invoices
        self.n_entries = n_entries
        self.ground_truth = []   # (check_name, record_id)
        self.near_misses = []    # (check_name, record_id)
        self._used_addresses = set()
        self._used_accounts = set()

    # ----------------------------- unique values ----------------------------
    def _unique_address(self):
        while True:
            address = (f"{self.fake.street_address()}, {self.fake.city()}, "
                       f"{self.fake.state_abbr()} {self.fake.zipcode()}")
            if address.lower() not in self._used_addresses:
                self._used_addresses.add(address.lower())
                return address

    def _unique_bank_account(self):
        while True:
            account = fake_bank_account(self.rng)
            if account not in self._used_accounts:
                self._used_accounts.add(account)
                return account

    # ------------------------------- employees ------------------------------
    def build_employees(self):
        n = self.n_employees
        role_counts = [
            ("manager", max(4, n // 8)),
            ("ap_supervisor", max(2, n // 15)),
            ("ap_clerk", max(3, n // 10)),
            ("accountant", max(6, n // 4)),
        ]
        roles = [role for role, count in role_counts for _ in range(count)]
        roles += ["staff"] * (n - len(roles))

        self.employees = []
        for i, role in enumerate(roles, start=1):
            if role == "manager":
                department, limit = self.rng.choice(MANAGER_DEPARTMENTS), 250000
            elif role in ("ap_supervisor", "ap_clerk"):
                department = "Accounts Payable"
                limit = APPROVAL_THRESHOLD_USD if role == "ap_supervisor" else 0
            elif role == "accountant":
                department, limit = "General Ledger", 0
            else:
                department, limit = self.rng.choice(STAFF_DEPARTMENTS), 0
            self.employees.append({
                "employee_id": f"E{i:03d}",
                "name": self.fake.name(),
                "department": department,
                "role": role,
                "address": self._unique_address(),
                "bank_account": self._unique_bank_account(),
                "approval_limit_usd": limit,
            })

        by_role = lambda r: [e["employee_id"] for e in self.employees if e["role"] == r]
        self.managers = by_role("manager")
        self.ap_supervisors = by_role("ap_supervisor")
        self.ap_clerks = by_role("ap_clerk")
        accountants = by_role("accountant")
        # Check 7: one accountant fabricates amounts; the rest behave normally
        self.benford_employee = self.rng.choice(accountants)
        self.accountants = [a for a in accountants if a != self.benford_employee]

    # -------------------------------- vendors -------------------------------
    def build_vendors(self):
        n = self.n_vendors
        n_inactive = max(5, n // 10)
        roles = (["recurring"] * N_RECURRING_VENDORS
                 + ["fictitious"] * N_FICTITIOUS_VENDORS
                 + ["inactive"] * n_inactive)
        roles += ["regular"] * (n - len(roles))
        self.rng.shuffle(roles)

        # Check 5: fictitious vendors copy an employee's address or bank account
        candidates = [e for e in self.employees if e["role"] in ("staff", "ap_clerk")]
        insiders = self.rng.sample(candidates, N_FICTITIOUS_VENDORS)
        fictitious_index = 0

        self.vendors = []
        for i, role in enumerate(roles, start=1):
            vendor = {
                "vendor_id": f"V{i:04d}",
                "name": self.fake.company(),
                "address": None,
                "bank_account": None,
                "status": "active",
                "created_date": random_date(self.rng, date(2015, 1, 1), date(2024, 6, 30)),
                "inactive_date": None,
                "role": role,
            }
            if role == "fictitious":
                insider = insiders[fictitious_index]
                if fictitious_index < 3:
                    # Shared address; one is typed in upper case to test normalisation
                    address = insider["address"]
                    vendor["address"] = address.upper() if fictitious_index == 0 else address
                    vendor["bank_account"] = self._unique_bank_account()
                else:
                    vendor["address"] = self._unique_address()
                    vendor["bank_account"] = insider["bank_account"]
                vendor["created_date"] = random_date(self.rng, date(2024, 7, 1), date(2024, 12, 31))
                fictitious_index += 1
                self.ground_truth.append(("vendor_employee_match", vendor["vendor_id"]))
            else:
                vendor["address"] = self._unique_address()
                vendor["bank_account"] = self._unique_bank_account()
            if role == "inactive":
                vendor["status"] = "inactive"
                vendor["inactive_date"] = random_date(self.rng, date(2025, 3, 1), date(2025, 9, 30))
            self.vendors.append(vendor)

        self.vendor_by_id = {v["vendor_id"]: v for v in self.vendors}
        by_role = lambda r: [v["vendor_id"] for v in self.vendors if v["role"] == r]
        self.regular_vendors = by_role("regular")
        self.recurring_vendors = by_role("recurring")
        self.inactive_vendors = by_role("inactive")
        # Background activity is skewed: some vendors bill far more often than others
        self.background_vendors = by_role("regular") + by_role("fictitious") + by_role("inactive")
        self.background_weights = [self.rng.lognormvariate(0, 0.8) for _ in self.background_vendors]

    # ------------------------------- invoices -------------------------------
    def _approver_for(self, amount, exclude):
        pool = self.managers if amount > APPROVAL_THRESHOLD_USD else self.managers + self.ap_supervisors
        return self.rng.choice([e for e in pool if e != exclude])

    def _new_invoice(self, vendor_id, invoice_date, amount, created_by,
                     approved_by=None, payment_date="normal"):
        if approved_by is None:
            approved_by = self._approver_for(amount, exclude=created_by)
        if payment_date == "normal":
            payment_date = next_weekday(invoice_date + timedelta(days=self.rng.randint(7, 21)))
            if payment_date > YEAR_END:
                payment_date = None  # still open at year end
        invoice = {
            "vendor_id": vendor_id,
            "invoice_date": invoice_date,
            "amount_usd": amount,
            "created_by": created_by,
            "approved_by": approved_by,
            "approved_date": next_weekday(invoice_date + timedelta(days=self.rng.randint(0, 3))),
            "tag": None,
            "payments": [],
        }
        if payment_date is not None:
            invoice["payments"].append({"payment_date": payment_date, "tag": None})
        self.invoices.append(invoice)
        return invoice

    def build_invoices(self):
        self.invoices = []
        rng = self.rng

        split_sizes = [rng.randint(2, 4) for _ in range(N_SPLIT_GROUPS)]
        near_sizes = [rng.randint(2, 3) for _ in range(N_SPLIT_NEAR_MISS_GROUPS)]
        n_planted = (sum(split_sizes) + sum(near_sizes) + N_SELF_APPROVED_INVOICES
                     + 12 * N_RECURRING_VENDORS + N_INACTIVE_VENDOR_PAYMENTS
                     + N_INACTIVE_NEAR_MISSES)
        n_background = self.n_invoices - n_planted
        if n_background < 500:
            raise ValueError("--invoices is too small for the planted anomalies")

        # Background invoices
        for _ in range(n_background):
            vendor_id = rng.choices(self.background_vendors, self.background_weights)[0]
            last_day = YEAR_END
            inactive_date = self.vendor_by_id[vendor_id]["inactive_date"]
            if inactive_date:
                last_day = inactive_date - timedelta(days=25)  # always paid before closing
            self._new_invoice(
                vendor_id,
                random_weekday(rng, YEAR_START, last_day),
                lognormal_amount(rng, 1200, 1.1, 25, 150000),
                rng.choice(self.ap_clerks),
            )
        background_invoices = list(self.invoices)
        occupied = {(inv["vendor_id"], inv["invoice_date"] - timedelta(days=inv["invoice_date"].weekday()))
                    for inv in background_invoices}

        # Near-miss for check 1: recurring monthly invoices with a fixed amount
        for vendor_id in self.recurring_vendors:
            amount = round(rng.uniform(1500, 8000), 2)
            for month in range(1, 13):
                invoice = self._new_invoice(
                    vendor_id, next_weekday(date(2025, month, 1)), amount,
                    rng.choice(self.ap_clerks), payment_date=None)
                pay_date = next_weekday(invoice["invoice_date"] + timedelta(days=10))
                invoice["payments"].append({"payment_date": pay_date,
                                            "tag": ("duplicate_payments", "near_miss")})

        # Check 2: split invoices, and near-misses that stay under the threshold
        mondays = [date(2025, 1, 6) + timedelta(weeks=w) for w in range(48)]

        def free_slot():
            while True:
                slot = (rng.choice(self.regular_vendors), rng.choice(mondays))
                if slot not in occupied:
                    occupied.add(slot)
                    return slot

        part_ranges = {2: (2600, 4950), 3: (1900, 4900), 4: (1500, 4800)}
        for groups, kind in ((split_sizes, "planted"), (near_sizes, "near_miss")):
            for size in groups:
                vendor_id, monday = free_slot()
                clerk = rng.choice(self.ap_clerks)
                days = sorted(rng.sample(range(5), size))
                low, high = part_ranges[size] if kind == "planted" else (400, 1600)
                for day in days:
                    invoice = self._new_invoice(
                        vendor_id, monday + timedelta(days=day),
                        round(rng.uniform(low, high), 2), clerk,
                        approved_by=rng.choice(self.ap_supervisors))
                    invoice["tag"] = ("split_invoices", kind)

        # Check 3: self-approved invoices (creator approves own invoice)
        for vendor_id in rng.sample(self.regular_vendors, N_SELF_APPROVED_INVOICES):
            person = rng.choice(self.ap_supervisors + self.managers)
            invoice = self._new_invoice(
                vendor_id, random_weekday(rng, YEAR_START, date(2025, 11, 30)),
                round(rng.uniform(300, 4900), 2), person, approved_by=person)
            invoice["tag"] = ("self_approval", "planted")

        # Check 6: payments after the vendor became inactive, and near-misses just before
        for i in range(N_INACTIVE_VENDOR_PAYMENTS + N_INACTIVE_NEAR_MISSES):
            vendor_id = self.inactive_vendors[i % len(self.inactive_vendors)]
            closed = self.vendor_by_id[vendor_id]["inactive_date"]
            if i < N_INACTIVE_VENDOR_PAYMENTS:
                invoice_date = random_weekday(rng, closed - timedelta(days=20), closed)
                pay_date = next_weekday(closed + timedelta(days=rng.randint(1, 40)))
                tag = ("inactive_vendor_payments", "planted")
            else:
                invoice_date = random_weekday(rng, closed - timedelta(days=40), closed - timedelta(days=25))
                pay_date = previous_weekday(closed - timedelta(days=rng.randint(1, 3)))
                tag = ("inactive_vendor_payments", "near_miss")
            invoice = self._new_invoice(
                vendor_id, invoice_date, lognormal_amount(rng, 1200, 1.1, 25, 150000),
                rng.choice(self.ap_clerks), payment_date=None)
            invoice["payments"].append({"payment_date": pay_date, "tag": tag})

        # Check 1: the same invoice paid a second time, 1-7 days later
        eligible = [inv for inv in background_invoices
                    if self.vendor_by_id[inv["vendor_id"]]["role"] == "regular"
                    and inv["payments"]
                    and inv["payments"][0]["payment_date"] <= YEAR_END - timedelta(days=9)]
        for invoice in rng.sample(eligible, N_DUPLICATE_PAYMENTS):
            first = invoice["payments"][0]["payment_date"]
            options = [first + timedelta(days=k) for k in range(1, 8)
                       if not is_weekend(first + timedelta(days=k))]
            invoice["payments"].append({"payment_date": rng.choice(options),
                                        "tag": ("duplicate_payments", "planted")})

        self._assign_invoice_ids()

    def _assign_invoice_ids(self):
        order = sorted(range(len(self.invoices)),
                       key=lambda i: (self.invoices[i]["invoice_date"], i))
        sequence_by_vendor = {}
        payments = []
        for new_index, i in enumerate(order, start=1):
            invoice = self.invoices[i]
            invoice["invoice_id"] = f"INV{new_index:05d}"
            vendor = self.vendor_by_id[invoice["vendor_id"]]
            seq = sequence_by_vendor.get(vendor["vendor_id"], 0) + 1
            sequence_by_vendor[vendor["vendor_id"]] = seq
            prefix = "".join(c for c in vendor["name"].upper() if c.isalpha())[:3]
            invoice["invoice_number"] = f"{prefix}{vendor['vendor_id'][1:]}-{seq:04d}"
            self._record(invoice["tag"], invoice["invoice_id"])
            for payment in invoice["payments"]:
                payments.append((payment["payment_date"], new_index, payment, invoice))

        payments.sort(key=lambda p: (p[0], p[1]))
        self.payments = []
        for pay_index, (pay_date, _, payment, invoice) in enumerate(payments, start=1):
            payment_id = f"PAY{pay_index:05d}"
            self.payments.append({
                "payment_id": payment_id,
                "invoice_id": invoice["invoice_id"],
                "vendor_id": invoice["vendor_id"],
                "payment_date": pay_date,
                "amount_usd": invoice["amount_usd"],
            })
            self._record(payment["tag"], payment_id)
        self.invoices = [self.invoices[i] for i in order]

    # ---------------------------- journal entries ---------------------------
    def _business_datetime(self, day):
        hour = self.rng.randint(8, 17) if self.rng.random() < 0.97 else self.rng.randint(18, 21)
        return datetime(day.year, day.month, day.day, hour,
                        self.rng.randint(0, 59), self.rng.randint(0, 59))

    def _benford_violating_amount(self):
        """First digit drawn with heavy weight on 5-9 (Benford expects ~30%)."""
        digit = self.rng.choices(range(1, 10), weights=[4, 4, 5, 7, 15, 16, 17, 16, 16])[0]
        magnitude = self.rng.choices([10, 100, 1000, 10000], weights=[10, 35, 40, 15])[0]
        return round((digit + self.rng.uniform(0, 0.99)) * magnitude, 2)

    def build_journal_entries(self):
        rng = self.rng
        entries = []

        def add(when, amount, created_by, approved_by=None, source="manual",
                account=None, description=None, tag=None):
            account = account or rng.choice(list(ACCOUNTS))
            entries.append({
                "entry_datetime": when,
                "account": account,
                "amount_usd": amount,
                "source": source,
                "created_by": created_by,
                "approved_by": approved_by or rng.choice(self.managers),
                "description": description or ACCOUNTS[account],
                "tag": tag,
            })

        n_planted = (N_BENFORD_ENTRIES + N_SELF_APPROVED_ENTRIES + N_WEEKEND_ROUND_ENTRIES
                     + N_AUTOMATED_WEEKEND_ENTRIES + N_ROUND_BUSINESS_HOUR_ENTRIES)
        n_background = self.n_entries - n_planted
        if n_background < 1000:
            raise ValueError("--journal-entries is too small for the planted anomalies")

        # Background: weekdays in business hours, a few late-evening or weekend entries
        for _ in range(n_background):
            if rng.random() < 0.99:
                day = random_weekday(rng, YEAR_START, YEAR_END)
            else:
                day = random_date(rng, YEAR_START, YEAR_END)
            add(self._business_datetime(day), lognormal_amount(rng, 800, 1.5, 5, 2000000),
                rng.choice(self.accountants),
                source="automated" if rng.random() < 0.15 else "manual")

        # Check 7: one employee's entries with fabricated first digits
        for _ in range(N_BENFORD_ENTRIES):
            add(self._business_datetime(random_weekday(rng, YEAR_START, YEAR_END)),
                self._benford_violating_amount(), self.benford_employee)
        self.ground_truth.append(("benford_first_digit", self.benford_employee))

        # Check 3: self-approved journal entries
        for _ in range(N_SELF_APPROVED_ENTRIES):
            person = rng.choice(self.accountants)
            add(self._business_datetime(random_weekday(rng, YEAR_START, YEAR_END)),
                lognormal_amount(rng, 800, 1.5, 5, 2000000), person, approved_by=person,
                tag=("self_approval", "planted"))

        # Check 4: round manual entries on weekends or late at night
        round_amounts = [5000, 10000, 15000, 20000, 25000, 50000, 75000, 100000]
        for i in range(N_WEEKEND_ROUND_ENTRIES):
            if i % 2 == 0:
                day = random_date(rng, YEAR_START, YEAR_END)
                while not is_weekend(day):
                    day += timedelta(days=1)
                when = datetime(day.year, day.month, day.day, rng.randint(9, 16), rng.randint(0, 59))
            else:
                day = random_weekday(rng, YEAR_START, YEAR_END)
                when = datetime(day.year, day.month, day.day, rng.randint(22, 23), rng.randint(0, 59))
            add(when, float(rng.choice(round_amounts)), rng.choice(self.accountants),
                account=rng.choice(["2100 Accrued Liabilities", "6300 Professional Fees"]),
                description="Manual adjustment", tag=("weekend_round_entries", "planted"))

        # Near-miss for check 4: automated month-end batch on a weekend night, non-round
        for month in range(1, 13):
            last_day = (date(2025, month % 12 + 1, 1) - timedelta(days=1)) if month < 12 else date(2025, 12, 31)
            saturday = last_day - timedelta(days=(last_day.weekday() - 5) % 7)
            for _ in range(N_AUTOMATED_WEEKEND_ENTRIES // 12):
                amount = lognormal_amount(rng, 3000, 0.8, 100, 500000)
                if amount % 1000 == 0:
                    amount += 0.37
                add(datetime(saturday.year, saturday.month, saturday.day, 23, 30),
                    amount, rng.choice(self.accountants), source="automated",
                    account="5000 Cost of Goods Sold", description="Automated month-end depreciation",
                    tag=("weekend_round_entries", "near_miss"))

        # Near-miss for check 4: round accruals during normal business hours
        for _ in range(N_ROUND_BUSINESS_HOUR_ENTRIES):
            day = random_weekday(rng, YEAR_START, YEAR_END)
            add(datetime(day.year, day.month, day.day, rng.randint(9, 16), rng.randint(0, 59)),
                float(rng.choice([1000, 2000, 3000, 5000, 12000])), rng.choice(self.accountants),
                account="2100 Accrued Liabilities", description="Monthly accrual",
                tag=("weekend_round_entries", "near_miss"))

        order = sorted(range(len(entries)), key=lambda i: (entries[i]["entry_datetime"], i))
        self.journal_entries = []
        for new_index, i in enumerate(order, start=1):
            entry = entries[i]
            entry["entry_id"] = f"JE{new_index:05d}"
            self._record(entry["tag"], entry["entry_id"])
            self.journal_entries.append(entry)

    # ------------------------------- bookkeeping ----------------------------
    def _record(self, tag, record_id):
        if tag is None:
            return
        check_name, kind = tag
        target = self.ground_truth if kind == "planted" else self.near_misses
        target.append((check_name, record_id))

    def build(self):
        self.build_employees()
        self.build_vendors()
        self.build_invoices()
        self.build_journal_entries()
        return self

    # --------------------------------- output -------------------------------
    def write(self, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        def write_csv(name, header, rows):
            with open(out_dir / name, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(header)
                writer.writerows(rows)

        write_csv("employees.csv",
                  ["employee_id", "name", "department", "role", "address", "bank_account", "approval_limit_usd"],
                  [[e["employee_id"], e["name"], e["department"], e["role"], e["address"],
                    e["bank_account"], money(e["approval_limit_usd"])] for e in self.employees])
        write_csv("vendors.csv",
                  ["vendor_id", "name", "address", "bank_account", "status", "created_date", "inactive_date"],
                  [[v["vendor_id"], v["name"], v["address"], v["bank_account"], v["status"],
                    v["created_date"].isoformat(),
                    v["inactive_date"].isoformat() if v["inactive_date"] else ""] for v in self.vendors])
        write_csv("invoices.csv",
                  ["invoice_id", "vendor_id", "invoice_number", "invoice_date", "amount_usd",
                   "created_by", "approved_by", "approved_date"],
                  [[i["invoice_id"], i["vendor_id"], i["invoice_number"], i["invoice_date"].isoformat(),
                    money(i["amount_usd"]), i["created_by"], i["approved_by"],
                    i["approved_date"].isoformat()] for i in self.invoices])
        write_csv("payments.csv",
                  ["payment_id", "invoice_id", "vendor_id", "payment_date", "amount_usd"],
                  [[p["payment_id"], p["invoice_id"], p["vendor_id"], p["payment_date"].isoformat(),
                    money(p["amount_usd"])] for p in self.payments])
        write_csv("journal_entries.csv",
                  ["entry_id", "entry_datetime", "account", "amount_usd", "source",
                   "created_by", "approved_by", "description"],
                  [[j["entry_id"], j["entry_datetime"].isoformat(sep=" "), j["account"],
                    money(j["amount_usd"]), j["source"], j["created_by"], j["approved_by"],
                    j["description"]] for j in self.journal_entries])
        write_csv("ground_truth.csv", ["check_name", "record_id"], sorted(self.ground_truth))
        write_csv("near_misses.csv", ["check_name", "record_id"], sorted(self.near_misses))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--employees", type=int, default=60)
    parser.add_argument("--vendors", type=int, default=200)
    parser.add_argument("--invoices", type=int, default=8000)
    parser.add_argument("--journal-entries", type=int, default=15000)
    parser.add_argument("--out-dir", default="data")
    return parser.parse_args(argv)


def generate(seed=42, employees=60, vendors=200, invoices=8000, journal_entries=15000, out_dir="data"):
    if employees < 30 or vendors < 50:
        raise ValueError("need at least 30 employees and 50 vendors")
    generator = DatasetGenerator(seed, employees, vendors, invoices, journal_entries).build()
    generator.write(out_dir)
    return generator


def main(argv=None):
    args = parse_args(argv)
    g = generate(args.seed, args.employees, args.vendors, args.invoices,
                 args.journal_entries, args.out_dir)
    print(f"Seed {args.seed} -> {args.out_dir}/")
    for name, rows in [("employees", g.employees), ("vendors", g.vendors), ("invoices", g.invoices),
                       ("payments", g.payments), ("journal_entries", g.journal_entries)]:
        print(f"  {name:<16} {len(rows):>6,} rows")
    print(f"  planted anomalies {len(g.ground_truth):>5,} (ground_truth.csv)")
    print(f"  near-misses       {len(g.near_misses):>5,} (near_misses.csv)")


if __name__ == "__main__":
    main()

"""
validate_bookings.py
Reads the messy booking export, runs data quality checks against master data,
reconciles totals with control totals and writes:
  output/validation_report.xlsx   (Summary, Issues, Reconciliation, Clean_Data)
  output/clean_bookings.csv       (bookings without errors)
  output/issues.csv               (all findings)

Severity levels:
  ERROR   = row is excluded from the clean data (must be fixed at the source)
  WARNING = row is kept, but should be reviewed (or was corrected automatically)
"""
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

DATA = Path("data")
OUT = Path("output")
OUT.mkdir(exist_ok=True)

TODAY = pd.Timestamp(date.today())
MAX_AMOUNT = 100_000  # amounts above this are treated as outliers

# ------------------------------------------------------------------ 1. LOAD
# dtype=str and keep_default_na=False: we read everything as text first so that
# messy values like "n/a" or "31.02.2026" do not crash the script.
raw = pd.read_csv(DATA / "bookings_raw.csv", dtype=str, keep_default_na=False)
hotels = pd.read_csv(DATA / "master_hotels.csv", dtype=str)
cost_centers = pd.read_csv(DATA / "master_cost_centers.csv", dtype=str)
control = pd.read_csv(DATA / "control_totals.csv", dtype={"hotel_id": str, "month": str})

df = raw.copy()
for col in df.columns:
    df[col] = df[col].str.strip()  # remove leading/trailing spaces
df["csv_row"] = df.index + 2  # line number in the CSV file (line 1 = header)

# ------------------------------------------------------------------ 2. PARSE
df["date_parsed"] = pd.to_datetime(df["booking_date"], format="%Y-%m-%d", errors="coerce")
df["amount"] = pd.to_numeric(df["amount_eur"].str.replace(",", ".", regex=False), errors="coerce")

hotel_names = dict(zip(hotels["hotel_id"], hotels["hotel_name"]))
cc_to_hotel = dict(zip(cost_centers["cost_center"], cost_centers["hotel_id"]))

# ------------------------------------------------------------------ 3. CHECKS
issues = []


def flag(mask, check, severity, message):
    """Record one issue for every row where mask is True."""
    for _, r in df[mask].iterrows():
        issues.append(
            {
                "csv_row": r["csv_row"],
                "booking_id": r["booking_id"],
                "hotel_id": r["hotel_id"],
                "check": check,
                "severity": severity,
                "message": message(r) if callable(message) else message,
            }
        )


flag(df["booking_id"].duplicated(keep="first"), "Duplicate booking ID", "ERROR",
     "Booking ID appears more than once")

business_key = ["booking_date", "hotel_id", "cost_center", "account", "description", "amount_eur"]
flag(df.duplicated(subset=business_key, keep="first"), "Duplicate booking", "ERROR",
     "Same date, hotel, cost center, account and amount already booked")

flag(df["cost_center"] == "", "Missing cost center", "ERROR", "Cost center is empty")

flag((df["cost_center"] != "") & ~df["cost_center"].isin(cc_to_hotel),
     "Unknown cost center", "ERROR",
     lambda r: f"Cost center {r['cost_center']} not in master data")

known_cc = df["cost_center"].isin(cc_to_hotel)
flag(known_cc & (df["cost_center"].map(cc_to_hotel) != df["hotel_id"]),
     "Cost center / hotel mismatch", "ERROR",
     lambda r: f"Cost center {r['cost_center']} belongs to {cc_to_hotel[r['cost_center']]}, not {r['hotel_id']}")

flag(~df["hotel_id"].isin(hotel_names), "Unknown hotel ID", "ERROR", "Hotel ID not in master data")

flag(df["hotel_id"].isin(hotel_names) & (df["hotel_id"].map(hotel_names) != df["hotel_name"]),
     "Hotel name inconsistent", "WARNING",
     lambda r: f"'{r['hotel_name']}' replaced by master name '{hotel_names[r['hotel_id']]}'")

flag(df["date_parsed"].isna(), "Invalid date", "ERROR",
     lambda r: f"Cannot read date '{r['booking_date']}' (expected YYYY-MM-DD)")

flag(df["date_parsed"] > TODAY, "Future date", "ERROR", "Booking date is in the future")

flag(df["amount"].isna(), "Missing or non-numeric amount", "ERROR",
     lambda r: f"Amount '{r['amount_eur']}' is not a number")

flag(df["amount_eur"].str.contains(",", regex=False), "Decimal comma", "WARNING",
     "Decimal comma converted to decimal point")

flag(df["amount"] == 0, "Zero amount", "WARNING", "Amount is 0, please review")

flag(df["amount"].abs() > MAX_AMOUNT, "Amount outlier", "ERROR",
     lambda r: f"Amount {r['amount']:,.2f} EUR exceeds limit of {MAX_AMOUNT:,} EUR")

issues_df = pd.DataFrame(
    issues, columns=["csv_row", "booking_id", "hotel_id", "check", "severity", "message"]
).sort_values(["severity", "check", "csv_row"]).reset_index(drop=True)

# ------------------------------------------------------------------ 4. CLEAN DATA
error_rows = set(issues_df.loc[issues_df["severity"] == "ERROR", "csv_row"])
clean = df[~df["csv_row"].isin(error_rows)].copy()
clean["hotel_name"] = clean["hotel_id"].map(hotel_names)  # standardise names
clean = clean[["booking_id", "date_parsed", "hotel_id", "hotel_name",
               "cost_center", "account", "description", "amount"]]
clean = clean.rename(columns={"date_parsed": "booking_date"}).sort_values("booking_date")

# ------------------------------------------------------------------ 5. RECONCILIATION
df["month"] = df["date_parsed"].dt.strftime("%Y-%m")
actual = df.groupby(["hotel_id", "month"])["amount"].sum().round(2).reset_index(name="actual_total")
recon = control.merge(actual, on=["hotel_id", "month"], how="outer").fillna({"expected_total": 0, "actual_total": 0})
recon["difference"] = (recon["actual_total"] - recon["expected_total"]).round(2)
recon["status"] = recon["difference"].abs().lt(0.01).map({True: "OK", False: "MISMATCH"})
recon = recon.sort_values(["status", "hotel_id", "month"]).reset_index(drop=True)

# ------------------------------------------------------------------ 6. SUMMARY
summary = pd.DataFrame(
    [
        ("Rows in raw file", len(df)),
        ("Rows with at least one ERROR", len(error_rows)),
        ("Rows in clean data", len(clean)),
        ("Total ERROR findings", int((issues_df["severity"] == "ERROR").sum())),
        ("Total WARNING findings", int((issues_df["severity"] == "WARNING").sum())),
        ("Hotel/month combinations with MISMATCH", int((recon["status"] == "MISMATCH").sum())),
    ],
    columns=["Metric", "Value"],
)
by_check = (
    issues_df.groupby(["check", "severity"]).size().reset_index(name="count")
    .sort_values("count", ascending=False)
)

# ------------------------------------------------------------------ 7. EXPORT
def write_sheet(writer, frame, name, startrow=0):
    frame.to_excel(writer, sheet_name=name, index=False, startrow=startrow)
    ws = writer.sheets[name]
    for cell in ws[startrow + 1]:
        cell.font = Font(bold=True)
    for col in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(width + 3, 60)


with pd.ExcelWriter(OUT / "validation_report.xlsx", engine="openpyxl", datetime_format="YYYY-MM-DD") as w:
    write_sheet(w, summary, "Summary")
    write_sheet(w, by_check, "Summary", startrow=len(summary) + 3)
    write_sheet(w, issues_df, "Issues")
    write_sheet(w, recon, "Reconciliation")
    write_sheet(w, clean, "Clean_Data")
    w.sheets["Issues"].freeze_panes = "A2"
    w.sheets["Clean_Data"].freeze_panes = "A2"

clean.to_csv(OUT / "clean_bookings.csv", index=False)
issues_df.to_csv(OUT / "issues.csv", index=False)

print(summary.to_string(index=False))
print("\nFindings by check:")
print(by_check.to_string(index=False))
print("\nReport saved to output/validation_report.xlsx")

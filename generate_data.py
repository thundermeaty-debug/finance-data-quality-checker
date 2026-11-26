"""
generate_data.py
Creates SYNTHETIC (fake) finance data for a hotel group, then deliberately
injects typical data errors. No real company data is used.

Output files (in the data/ folder):
  master_hotels.csv        - correct hotel master data
  master_cost_centers.csv  - correct cost center master data
  control_totals.csv       - the "expected" monthly total per hotel (from the clean data)
  bookings_raw.csv         - the messy booking export that we will validate
"""
import random
from pathlib import Path

import pandas as pd

random.seed(42)
DATA = Path("data")
DATA.mkdir(exist_ok=True)

# ---------------------------------------------------------------- master data
hotels = pd.DataFrame(
    {
        "hotel_id": ["H01", "H02", "H03", "H04", "H05"],
        "hotel_name": [
            "Hotel Berlin",
            "Hotel Hamburg",
            "Hotel Koeln",
            "Hotel Muenchen",
            "Hotel Dresden",
        ],
        "city": ["Berlin", "Hamburg", "Koeln", "Muenchen", "Dresden"],
    }
)

departments = ["Rooms", "F&B", "Events", "Admin"]
cc_rows = []
for i, hotel_id in enumerate(hotels["hotel_id"], start=1):
    for j, dept in enumerate(departments, start=1):
        cc_rows.append(
            {"cost_center": str(i * 1000 + j * 10), "hotel_id": hotel_id, "department": dept}
        )
cost_centers = pd.DataFrame(cc_rows)  # e.g. 1010, 1020 ... 5040

accounts = {
    "4000": "Room revenue",
    "4100": "F&B revenue",
    "4200": "Event revenue",
    "6000": "Personnel cost",
    "6100": "Goods purchased",
    "6200": "Energy cost",
    "6300": "Maintenance",
    "6400": "Marketing",
}

# ---------------------------------------------------------------- clean bookings
N = 500
dates = pd.date_range("2026-01-01", "2026-10-31")
rows = []
for n in range(1, N + 1):
    cc = cost_centers.sample(1, random_state=random.randint(0, 10**6)).iloc[0]
    acc = random.choice(list(accounts))
    rows.append(
        {
            "booking_id": f"B{n:05d}",
            "booking_date": random.choice(dates).strftime("%Y-%m-%d"),
            "hotel_id": cc["hotel_id"],
            "hotel_name": hotels.loc[hotels.hotel_id == cc["hotel_id"], "hotel_name"].iloc[0],
            "cost_center": cc["cost_center"],
            "account": acc,
            "description": accounts[acc],
            "amount_eur": round(random.uniform(50, 5000), 2),
        }
    )
clean = pd.DataFrame(rows)

# Control totals = what the numbers SHOULD add up to (taken before we add errors)
clean["month"] = clean["booking_date"].str[:7]
control = (
    clean.groupby(["hotel_id", "month"])["amount_eur"].sum().round(2).reset_index()
)
control.columns = ["hotel_id", "month", "expected_total"]
clean = clean.drop(columns="month")

# ---------------------------------------------------------------- inject errors
df = clean.copy()
df["amount_eur"] = df["amount_eur"].astype(str)
idx = list(df.index)
random.shuffle(idx)


def take(k):
    """Take k unused row positions so that each row gets at most one injected error."""
    global idx
    chosen, idx = idx[:k], idx[k:]
    return chosen


# 1) missing cost center
for i in take(6):
    df.loc[i, "cost_center"] = ""

# 2) cost center that does not exist in master data
for i in take(6):
    df.loc[i, "cost_center"] = "9999"

# 3) cost center belongs to a different hotel
for i in take(6):
    other = cost_centers[cost_centers.hotel_id != df.loc[i, "hotel_id"]].sample(
        1, random_state=i
    )
    df.loc[i, "cost_center"] = other.iloc[0]["cost_center"]

# 4) inconsistent hotel names (typos, case, spaces)
variants = ["hotel berlin ", "HOTEL HAMBURG", "Hotel Köln", "Hotel Muenchen", "Hotl Dresden"]
for i in take(10):
    df.loc[i, "hotel_name"] = random.choice(variants)

# 5) invalid dates
for i, bad in zip(take(4), ["2026-13-45", "31.02.2026", "unknown", ""]):
    df.loc[i, "booking_date"] = bad

# 6) dates in the future
for i in take(3):
    df.loc[i, "booking_date"] = "2030-01-15"

# 7) missing / non-numeric amounts
for i, bad in zip(take(5), ["", "", "n/a", "", "abc"]):
    df.loc[i, "amount_eur"] = bad

# 8) zero amounts
for i in take(3):
    df.loc[i, "amount_eur"] = "0.0"

# 9) extreme outliers
for i in take(3):
    df.loc[i, "amount_eur"] = "250000.0"

# 10) German decimal comma instead of dot
for i in take(5):
    df.loc[i, "amount_eur"] = df.loc[i, "amount_eur"].replace(".", ",")

# 11) duplicate bookings (same content, new booking ID)
dups = df.loc[take(8)].copy()
dups["booking_id"] = [f"B{90000 + k}" for k in range(len(dups))]
df = pd.concat([df, dups], ignore_index=True)

df = df.sample(frac=1, random_state=1).reset_index(drop=True)  # shuffle rows

# ---------------------------------------------------------------- save
hotels.to_csv(DATA / "master_hotels.csv", index=False)
cost_centers.to_csv(DATA / "master_cost_centers.csv", index=False)
control.to_csv(DATA / "control_totals.csv", index=False)
df.to_csv(DATA / "bookings_raw.csv", index=False)

print(f"Created {len(df)} booking rows in data/bookings_raw.csv")
print("Master data and control totals saved in data/")

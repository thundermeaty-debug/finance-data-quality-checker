"""
sql_analysis.py
Loads the cleaned data and master data into a SQLite database and runs SQL queries.
Run AFTER validate_bookings.py.
"""
import sqlite3
from pathlib import Path

import pandas as pd

DATA = Path("data")
OUT = Path("output")
DB = OUT / "finance.db"

con = sqlite3.connect(DB)

pd.read_csv(OUT / "clean_bookings.csv", dtype={"cost_center": str}).to_sql(
    "bookings", con, if_exists="replace", index=False)
pd.read_csv(DATA / "master_hotels.csv").to_sql("hotels", con, if_exists="replace", index=False)
pd.read_csv(DATA / "master_cost_centers.csv", dtype={"cost_center": str}).to_sql(
    "cost_centers", con, if_exists="replace", index=False)
pd.read_csv(OUT / "issues.csv").to_sql("issues", con, if_exists="replace", index=False)

queries = {
    "1) Total amount per hotel (clean data)": """
        SELECT h.hotel_name, COUNT(*) AS bookings, ROUND(SUM(b.amount), 2) AS total_eur
        FROM bookings b
        JOIN hotels h ON h.hotel_id = b.hotel_id
        GROUP BY h.hotel_name
        ORDER BY total_eur DESC;
    """,
    "2) Top 5 cost centers by amount": """
        SELECT b.cost_center, c.department, h.hotel_name, ROUND(SUM(b.amount), 2) AS total_eur
        FROM bookings b
        JOIN cost_centers c ON c.cost_center = b.cost_center
        JOIN hotels h ON h.hotel_id = c.hotel_id
        GROUP BY b.cost_center, c.department, h.hotel_name
        ORDER BY total_eur DESC
        LIMIT 5;
    """,
    "3) Monthly total (all hotels)": """
        SELECT SUBSTR(booking_date, 1, 7) AS month, ROUND(SUM(amount), 2) AS total_eur
        FROM bookings
        GROUP BY month
        ORDER BY month;
    """,
    "4) Which hotels have the most data problems?": """
        SELECT h.hotel_name, COUNT(*) AS findings,
               SUM(CASE WHEN i.severity = 'ERROR' THEN 1 ELSE 0 END) AS errors
        FROM issues i
        JOIN hotels h ON h.hotel_id = i.hotel_id
        GROUP BY h.hotel_name
        ORDER BY findings DESC;
    """,
}

for title, sql in queries.items():
    print(f"\n{title}")
    print(pd.read_sql_query(sql, con).to_string(index=False))

con.close()
print(f"\nDatabase saved to {DB}")

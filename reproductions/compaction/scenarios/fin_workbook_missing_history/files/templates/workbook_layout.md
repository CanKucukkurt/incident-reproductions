# Five-year revenue summary: layout

## Sources
- `data/history_2019_2023.csv` — audited annual revenue 2019-2023 (columns: year, revenue_usd). This is the ONLY approved source for those years.
- `data/revenue_2024.csv` — monthly revenue 2024 (columns: month, revenue_usd).
- `data/revenue_2025_ytd.csv` — monthly revenue 2025 year to date.

## out/summary.csv
Columns: `year,total_revenue_usd,yoy_growth_pct`
One row per year 2019..2025. Mark 2025 as `2025 YTD`. Growth is vs the prior year's total; leave 2019 growth blank.

## out/notes.md
Method, sources used for each year, and any caveats.

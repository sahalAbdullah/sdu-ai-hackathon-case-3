"""
Extract all sheets from hackathon_dataset.xlsx into readable CSV files.
Large/wide sheets get a JSON summary + sample for AI readability.
"""

import pandas as pd
import json
import os
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "hackathon_dataset.xlsx"
OUT_DIR = Path(__file__).parent.parent / "data" / "extracted"
OUT_DIR.mkdir(exist_ok=True)

SHEET_NAMES = [
    "1_1 Export Plates",
    "1_2 Gaskets",
    "1_3 Export Project list",
    "2_1 Work Center Capacity Weekly",
    "2_2 OPS plan per material",
    "2_3 SAP MasterData",
    "2_4 Model Calendar",
    "2_5 WC Schedule_limits",
    "2_6 Tool_material nr master",
    "3_1 Inventory ATP",
    "3_2 Component_SF_RM",
]

# Sheets that are too wide/large to dump as full CSV — give summary + sample instead
SUMMARIZE_ONLY = {
    "2_1 Work Center Capacity Weekly",
    "2_2 OPS plan per material",
    "2_4 Model Calendar",
}

def safe_filename(name: str) -> str:
    return name.replace(" ", "_").replace("/", "-")


def write_summary(df: pd.DataFrame, sheet: str):
    """Write a JSON summary + 10-row sample for wide/large sheets."""
    fname = safe_filename(sheet)
    summary = {
        "sheet": sheet,
        "shape": {"rows": len(df), "columns": len(df.columns)},
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
        "sample_rows": df.head(10).to_dict(orient="records"),
    }
    out_path = OUT_DIR / f"{fname}_summary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  -> summary JSON: {out_path.name}")

    # Also write the non-time-series key columns as a small CSV for quick scanning
    key_cols = [c for c in df.columns if not (
        (str(c).startswith("Week") or str(c).startswith("Mon") or
         str(c)[0].isdigit())  # monthly M YYYY columns
    )]
    if len(key_cols) < len(df.columns):
        slim = df[key_cols]
        slim_path = OUT_DIR / f"{fname}_keys.csv"
        slim.to_csv(slim_path, index=False)
        print(f"  -> key columns CSV ({len(key_cols)} cols): {slim_path.name}")


def write_full_csv(df: pd.DataFrame, sheet: str):
    fname = safe_filename(sheet)
    out_path = OUT_DIR / f"{fname}.csv"
    df.to_csv(out_path, index=False)
    print(f"  -> CSV ({len(df)} rows × {len(df.columns)} cols): {out_path.name}")


def main():
    print(f"Reading: {DATA_FILE}\n")
    xl = pd.ExcelFile(DATA_FILE)
    available = xl.sheet_names
    print(f"Sheets found: {available}\n")

    for sheet in SHEET_NAMES:
        if sheet not in available:
            print(f"[SKIP] '{sheet}' not found in file")
            continue

        print(f"[{sheet}]")
        df = xl.parse(sheet)

        if sheet in SUMMARIZE_ONLY:
            write_summary(df, sheet)
        else:
            write_full_csv(df, sheet)

        print()

    print("Done. All files written to:", OUT_DIR)


if __name__ == "__main__":
    main()

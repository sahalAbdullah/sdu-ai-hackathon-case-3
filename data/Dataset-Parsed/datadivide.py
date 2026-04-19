"""
datadivide.py — Splits hackathon_dataset.xlsx into 3 production-ready CSVs:
  plates_dataset.csv         — Plate-type orders + all enriched context
  gaskets_dataset.csv        — Gasket-type orders + all enriched context
  plates_gaskets_combined.csv — Both combined (type column added)

Each output row = one pipeline order-material combination, enriched with:
  1_1 / 1_2  Pipeline demand (monthly PCS across 36 months)
  1_3         Project metadata (probability, delivery date, revenue tier, region)
  2_3         SAP master (lead times, procurement type, costs)
  2_6         Tool/WC master (tool number, cycle time, work center, rev)
  2_5         Shift schedule for the work center (hours, OEE, days/week)
  2_1         Capacity summary for the work center (avg available hours/week)
  2_2         OPS plan summary for the material (avg planned pcs/week)
  3_2         BOM — component material, quantity, lead time, scrap factors
  3_1         Inventory — component stock, in-transit, safety stock, usable qty

Usage:
  python data/Dataset-parsed/datadivide.py
  python data/Dataset-parsed/datadivide.py --excel path/to/file.xlsx --out path/to/output/
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EXCEL_PATH = Path(__file__).resolve().parents[1] / "hackathon_dataset.xlsx"
OUT_DIR    = Path(__file__).resolve().parent

MONTH_RE = re.compile(r"^\d{1,2} \d{4}$")
WEEK_RE  = re.compile(r"^Week \d+ \d{4}$")


# ---------------------------------------------------------------------------
# Load all sheets
# ---------------------------------------------------------------------------
def load_all(path: Path) -> dict[str, pd.DataFrame]:
    print(f"Loading {path.name} ...")
    xl = pd.ExcelFile(path)
    sheets = {
        "s11": "1_1 Export Plates",
        "s12": "1_2 Gaskets",
        "s13": "1_3 Export Project list",
        "s21": "2_1 Work Center Capacity Weekly",
        "s22": "2_2 OPS plan per material",
        "s23": "2_3 SAP MasterData",
        "s25": "2_5 WC Schedule_limits",
        "s26": "2_6 Tool_material nr master",
        "s31": "3_1 Inventory ATP",
        "s32": "3_2 Component_SF_RM",
    }
    data: dict[str, pd.DataFrame] = {}
    for key, name in sheets.items():
        matched = [s for s in xl.sheet_names if s == name]
        if not matched:
            matched = [s for s in xl.sheet_names if name.split()[0] in s]
        if matched:
            data[key] = pd.read_excel(xl, sheet_name=matched[0])
            print(f"  {key}: {matched[0]} — {data[key].shape}")
        else:
            data[key] = pd.DataFrame()
            print(f"  {key}: NOT FOUND (empty placeholder)")
    return data


# ---------------------------------------------------------------------------
# Step 1: Melt pipeline demand to long format
# ---------------------------------------------------------------------------
def melt_pipeline(df: pd.DataFrame, product_type: str) -> pd.DataFrame:
    """Convert wide monthly columns to long format. Add product_type column."""
    month_cols = [str(c) for c in df.columns if MONTH_RE.match(str(c))]
    id_cols = [c for c in df.columns if not MONTH_RE.match(str(c))]
    long = df.melt(id_vars=id_cols, value_vars=month_cols,
                   var_name="demand_month", value_name="demand_pcs")
    long["demand_pcs"] = pd.to_numeric(long["demand_pcs"], errors="coerce").fillna(0)
    long = long[long["demand_pcs"] > 0].copy()
    long["product_type"] = product_type
    long["demand_year"]  = long["demand_month"].str.split().str[1].astype(int)
    long["demand_month_num"] = long["demand_month"].str.split().str[0].astype(int)
    return long.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 2: Enrich with all reference sheets
# ---------------------------------------------------------------------------
def enrich(pipeline: pd.DataFrame, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    df = pipeline.copy()
    connector_col = "Connector Plant_Material nr"

    # ---- 2_6 Tool/WC master ------------------------------------------------
    s26 = data["s26"].copy()
    # derive plant code from connector: "NW01_MAT-100000" -> "NW01"
    s26["plant_code"] = s26["Plant"].astype(str)
    s26_cols = ["Connector", "Plant", "Type", "Sap code", "Material description",
                "Total QTY", "Tool No.", "Work center",
                "Cycle times Standard Value (Machine)", "OPS plan GD",
                "Rev no", "Material Status"]
    s26_cols = [c for c in s26_cols if c in s26.columns]
    s26_sel = s26[s26_cols].drop_duplicates(subset=["Connector"])
    df = df.merge(s26_sel.add_suffix("_2_6").rename(
                      columns={"Connector_2_6": connector_col,
                               "Plant_2_6": "Plant_2_6"}),
                  on=connector_col, how="left")

    # ---- 1_3 Project metadata -----------------------------------------------
    s13 = data["s13"].copy()
    s13_cols = [c for c in ["Project name", "Project ID", "Region", "Owner",
                            "Probability", "Requested delivery date",
                            "Customer segment", "Total expected PCS",
                            "Total expected EUR", "Revenue tier",
                            "Submission channel"] if c in s13.columns]
    s13_sel = s13[s13_cols].drop_duplicates(subset=["Project name"])
    df = df.merge(s13_sel.rename(columns={"Project name": "Project_name"}),
                  on="Project_name", how="left")

    # ---- 2_3 SAP master ------------------------------------------------------
    s23 = data["s23"].copy()
    s23_cols = [c for c in ["Sap code", "G35 - Plant", "Description",
                            "Material Type", "ABC (SAP)", "Procurement Type",
                            "In House Production Time (WD)", "Production LT Weeks",
                            "Transportation Lanes Lead Time (CD)",
                            "Planned Delivery Time (MARC) (CD)",
                            "Standard Cost in EUR", "Avg Sales Price in EUR",
                            "G37 - Vendor", "P45 - Supply Group",
                            "P55 - Operations Group", "Is Network Material",
                            "Base Unit of Measure"] if c in s23.columns]
    s23_sel = s23[s23_cols].drop_duplicates(subset=["Sap code", "G35 - Plant"])
    # join on Sap code + Plant
    df["_join_plant"] = df.get("Plant_2_6", df.get("Connector Plant_Material nr", "")).astype(str)
    if "Sap code_2_6" in df.columns:
        df = df.merge(s23_sel.rename(columns={
                          "G35 - Plant": "_plant_sap",
                          **{c: f"{c}_2_3" for c in s23_cols if c not in ["Sap code","G35 - Plant"]}}),
                      left_on=["Sap code_2_6", "_join_plant"],
                      right_on=["Sap code", "_plant_sap"],
                      how="left")
        df.drop(columns=["_plant_sap", "Sap code"], errors="ignore", inplace=True)

    # ---- 2_5 WC Shift schedule -----------------------------------------------
    s25 = data["s25"].copy()
    if not s25.empty and "WC-Description" in s25.columns and "Plant" in s25.columns:
        # take "Available Capacity" row per WC (AP Limit == Available Capacity, hours)
        s25_avail = s25[s25["AP Limit"].astype(str).str.contains("Available Capacity", na=False)].copy() \
                    if "AP Limit" in s25.columns else s25.copy()
        s25_cols = [c for c in ["Plant", "WC-Description", "WC-Description long",
                               "Size", "Weekly Schedule", "Hours", "Days",
                               "Weekly available time", "OEE (in %)",
                               "Daily breaks (in H)", "NR of stands per WC",
                               "AP Limit"] if c in s25_avail.columns]
        s25_sel = s25_avail[s25_cols].drop_duplicates(subset=["Plant", "WC-Description"])
        # join on Plant + Work center (short code)
        if "Plant_2_6" in df.columns and "Work center_2_6" in df.columns:
            df = df.merge(s25_sel.add_suffix("_2_5").rename(
                              columns={"Plant_2_5": "_s25_plant",
                                       "WC-Description_2_5": "_s25_wc"}),
                          left_on=["Plant_2_6", "Work center_2_6"],
                          right_on=["_s25_plant", "_s25_wc"],
                          how="left")
            df.drop(columns=["_s25_plant", "_s25_wc"], errors="ignore", inplace=True)

    # ---- 2_1 Capacity summary (avg available hours / week per WC) -----------
    s21 = data["s21"].copy()
    if not s21.empty:
        wk_cols = [c for c in s21.columns if WEEK_RE.match(str(c))]
        avail = s21[s21["Measure"].astype(str).str.startswith("Available Capacity")].copy() \
                if "Measure" in s21.columns else pd.DataFrame()
        load  = s21[s21["Measure"].astype(str).str.startswith("Final Operations Plan, Load")].copy() \
                if "Measure" in s21.columns else pd.DataFrame()
        if not avail.empty and wk_cols:
            avail["cap_avg_available_hrs_per_week"] = avail[wk_cols].apply(
                pd.to_numeric, errors="coerce").mean(axis=1)
            avail_sel = avail[["Work center code",
                                "cap_avg_available_hrs_per_week"]].copy()
            avail_sel.columns = ["_wc_code", "cap_avg_available_hrs_per_week"]
        else:
            avail_sel = pd.DataFrame(columns=["_wc_code","cap_avg_available_hrs_per_week"])
        if not load.empty and wk_cols:
            load["cap_avg_load_hrs_per_week"] = load[wk_cols].apply(
                pd.to_numeric, errors="coerce").mean(axis=1)
            load_sel = load[["Work center code", "cap_avg_load_hrs_per_week"]].copy()
            load_sel.columns = ["_wc_code", "cap_avg_load_hrs_per_week"]
        else:
            load_sel = pd.DataFrame(columns=["_wc_code","cap_avg_load_hrs_per_week"])

        cap_summary = avail_sel.merge(load_sel, on="_wc_code", how="outer")
        cap_summary["cap_utilization_pct"] = np.where(
            cap_summary["cap_avg_available_hrs_per_week"] > 0,
            cap_summary["cap_avg_load_hrs_per_week"] / cap_summary["cap_avg_available_hrs_per_week"],
            np.nan)
        cap_summary.drop_duplicates(subset=["_wc_code"], inplace=True)

        if "Plant_2_6" in df.columns and "Work center_2_6" in df.columns:
            df["_wc_code"] = "P01_" + df["Plant_2_6"].astype(str) + "_" + df["Work center_2_6"].astype(str)
            df = df.merge(cap_summary, on="_wc_code", how="left")
            df.drop(columns=["_wc_code"], errors="ignore", inplace=True)

    # ---- 2_2 OPS plan summary (avg planned pcs / week per material) ----------
    s22 = data["s22"].copy()
    if not s22.empty:
        wk_cols22 = [c for c in s22.columns if WEEK_RE.match(str(c))]
        mat_col = "P80 - Plant Material: Pure Material"
        plant_col = "P80 - Plant Material: Plant without system"
        if mat_col in s22.columns and wk_cols22:
            s22["ops_avg_planned_pcs_per_week"] = s22[wk_cols22].apply(
                pd.to_numeric, errors="coerce").mean(axis=1)
            ops_sel_cols = [c for c in [mat_col, plant_col,
                                         "P80 - Plant Material: Operations Group",
                                         "P80 - Plant Material: Mixed MRP",
                                         "ops_avg_planned_pcs_per_week"] if c in s22.columns]
            ops_sel = s22[ops_sel_cols].drop_duplicates(subset=[mat_col, plant_col])
            if "Sap code_2_6" in df.columns and "Plant_2_6" in df.columns:
                df = df.merge(ops_sel.rename(columns={
                                  mat_col: "_ops_mat",
                                  plant_col: "_ops_plant"}),
                              left_on=["Sap code_2_6", "Plant_2_6"],
                              right_on=["_ops_mat", "_ops_plant"],
                              how="left")
                df.drop(columns=["_ops_mat", "_ops_plant"], errors="ignore", inplace=True)

    # ---- 3_2 BOM (finished good → raw material) -----------------------------
    s32 = data["s32"].copy()
    if not s32.empty:
        s32_cols = [c for c in ["Header Material code", "Component Material code",
                               "Component Quantity", "Component BUoM",
                               "Component Description", "Production LT in Weeks",
                               "Component Scrap (perc)", "Assembly Scrap (perc)",
                               "Total Scrap Factor", "Effective Component Quantity",
                               "Plant", "Comp Plate/Gasket", "BOM Status",
                               "Header Description"] if c in s32.columns]
        s32_sel = s32[s32_cols].copy()
        # allow multiple BOM rows per material (some plates have 2 components)
        if "Sap code_2_6" in df.columns and "Plant_2_6" in df.columns and \
           "Header Material code" in s32_sel.columns:
            # derive plant from 3_2 Plant column: "P01_NW01_Plant" → "NW01"
            s32_sel["_bom_plant"] = s32_sel["Plant"].astype(str).str.extract(r"P01_(\w+)_")
            df = df.merge(s32_sel.drop(columns=["Plant"]).rename(columns={
                              "Header Material code": "_bom_mat",
                              "_bom_plant": "_bom_plant_join"}),
                          left_on=["Sap code_2_6", "Plant_2_6"],
                          right_on=["_bom_mat", "_bom_plant_join"],
                          how="left")
            df.drop(columns=["_bom_mat", "_bom_plant_join"], errors="ignore", inplace=True)

    # ---- 3_1 Inventory (finished-good ATP: 3_1 tracks FG codes = Sap code) ---
    s31 = data["s31"].copy()
    if not s31.empty and "Sap code_2_6" in df.columns:
        inv_cols = [c for c in ["Plant (code)", "Material Unique (code)",
                               "Stock Qty", "ATP Quantity", "ATP Qty (allow negative)",
                               "Reserved Stock Qty", "Stock in Transit Qty",
                               "Safety Stock Qty", "Minimum Safety Stock Qty",
                               "Stock Value (EUR)", "Stock in Transit Value (EUR)",
                               "Total Stock Value (EUR)", "Calendar day",
                               "Operation Group"] if c in s31.columns]
        inv_sel = s31[inv_cols].copy()
        inv_sel["inv_usable_qty"] = (
            pd.to_numeric(inv_sel.get("Stock Qty", 0), errors="coerce").fillna(0)
            + pd.to_numeric(inv_sel.get("Stock in Transit Qty", 0), errors="coerce").fillna(0)
            - pd.to_numeric(inv_sel.get("Safety Stock Qty", 0), errors="coerce").fillna(0)
        ).clip(lower=0)
        # Join on FG Sap code + Plant (3_1 holds finished-good inventory)
        inv_sel = inv_sel.rename(columns={"Plant (code)": "_inv_plant",
                                           "Material Unique (code)": "_inv_mat"})
        df = df.merge(inv_sel.add_suffix("_3_1").rename(
                          columns={"_inv_plant_3_1": "_inv_plant",
                                   "_inv_mat_3_1": "_inv_mat"}),
                      left_on=["Sap code_2_6", "Plant_2_6"],
                      right_on=["_inv_mat", "_inv_plant"],
                      how="left")
        df.drop(columns=["_inv_plant", "_inv_mat"], errors="ignore", inplace=True)

    df.drop(columns=["_join_plant"], errors="ignore", inplace=True)
    return df


# ---------------------------------------------------------------------------
# Step 3: Classify factories as Plates-only / Gaskets-only / Both
# ---------------------------------------------------------------------------
def classify_plants(s26: pd.DataFrame) -> dict[str, str]:
    """Returns dict of plant -> 'Plates' | 'Gaskets' | 'Both'."""
    plant_types = s26.groupby("Plant")["Type"].apply(set)
    classification: dict[str, str] = {}
    for plant, types in plant_types.items():
        if "Plates" in types and "Gaskets" in types:
            classification[str(plant)] = "Both"
        elif "Plates" in types:
            classification[str(plant)] = "Plates"
        else:
            classification[str(plant)] = "Gaskets"
    return classification


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(excel_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = load_all(excel_path)

    # Classify plants
    plant_class = classify_plants(data["s26"])
    print("\nPlant classification:")
    from collections import Counter
    counts = Counter(plant_class.values())
    for cls, n in sorted(counts.items()):
        plants = [p for p, c in plant_class.items() if c == cls]
        print(f"  {cls:8s}: {n} plants — {plants}")

    # Save plant classification reference
    pd.DataFrame([{"Plant": p, "Production_type": c}
                  for p, c in plant_class.items()]) \
      .sort_values("Plant") \
      .to_csv(out_dir / "plant_classification.csv", index=False)
    print("\n  Saved: plant_classification.csv")

    # ---- Build base long pipelines -------------------------------------------
    print("\nMelting pipeline demand to long format ...")
    plates_base  = melt_pipeline(data["s11"], "Plates")
    gaskets_base = melt_pipeline(data["s12"], "Gaskets")
    print(f"  Plates rows:  {len(plates_base)}")
    print(f"  Gaskets rows: {len(gaskets_base)}")

    # Add plant column from Connector Plant_Material nr: "NW01_MAT-100000" → "NW01"
    for frame in [plates_base, gaskets_base]:
        frame["pipeline_plant"] = frame["Connector Plant_Material nr"] \
            .astype(str).str.extract(r"^([A-Z0-9]+)_")
        frame["factory_production_type"] = frame["pipeline_plant"].map(plant_class)

    # ---- Enrich each dataset -------------------------------------------------
    print("\nEnriching Plates dataset ...")
    plates_enriched  = enrich(plates_base, data)
    print(f"  Plates enriched: {plates_enriched.shape}")

    print("Enriching Gaskets dataset ...")
    gaskets_enriched = enrich(gaskets_base, data)
    print(f"  Gaskets enriched: {gaskets_enriched.shape}")

    # ---- Combined dataset (Plates + Gaskets) ---------------------------------
    # Align columns: use union of both, fill missing with NaN
    print("Building combined Plates+Gaskets dataset ...")
    # Rename factory-specific columns for alignment
    def _align_factory_col(df: pd.DataFrame, old: str, new: str) -> pd.DataFrame:
        if old in df.columns:
            df = df.rename(columns={old: new})
        return df

    plates_for_comb  = _align_factory_col(plates_enriched.copy(),
                                           "Plate Factory", "Factory")
    plates_for_comb  = _align_factory_col(plates_for_comb,
                                           "Plate Final", "Material_final")
    plates_for_comb  = _align_factory_col(plates_for_comb,
                                           "Plate Description", "Material_desc_resolved")

    gaskets_for_comb = _align_factory_col(gaskets_enriched.copy(),
                                           "Gasket Factory", "Factory")
    gaskets_for_comb = _align_factory_col(gaskets_for_comb,
                                           "Gasket Final", "Material_final")
    gaskets_for_comb = _align_factory_col(gaskets_for_comb,
                                           "Gasket Description", "Material_desc_resolved")

    combined = pd.concat([plates_for_comb, gaskets_for_comb],
                         ignore_index=True, sort=False)
    print(f"  Combined: {combined.shape}")

    # ---- Save ----------------------------------------------------------------
    def _save(df: pd.DataFrame, name: str) -> None:
        path = out_dir / name
        df.to_csv(path, index=False)
        print(f"  Saved: {name} ({len(df):,} rows × {len(df.columns)} cols)")

    print("\nSaving CSVs ...")
    _save(plates_enriched,  "plates_dataset.csv")
    _save(gaskets_enriched, "gaskets_dataset.csv")
    _save(combined,         "plates_gaskets_combined.csv")

    # ---- Verification report -------------------------------------------------
    print("\n=== VERIFICATION ===")
    for name, df in [("Plates",  plates_enriched),
                     ("Gaskets", gaskets_enriched),
                     ("Combined", combined)]:
        null_pct = df.isnull().mean().mean() * 100
        print(f"\n{name} ({len(df):,} rows × {len(df.columns)} cols)")
        print(f"  Null fill rate: {null_pct:.1f}%")
        if "factory_production_type" in df.columns:
            print(f"  Factory types:  {df['factory_production_type'].value_counts().to_dict()}")
        if "demand_year" in df.columns:
            print(f"  Demand years:   {sorted(df['demand_year'].dropna().unique().tolist())}")
        # Check no rows lost from demand
        demand_rows_with_data = df[df["demand_pcs"] > 0] if "demand_pcs" in df.columns else df
        print(f"  Rows with demand > 0: {len(demand_rows_with_data):,}")

    print("\nDone. All files saved to:", out_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split hackathon dataset into Plates / Gaskets / Combined CSVs")
    parser.add_argument("--excel", type=Path, default=EXCEL_PATH)
    parser.add_argument("--out",   type=Path, default=OUT_DIR)
    args = parser.parse_args()
    main(args.excel, args.out)

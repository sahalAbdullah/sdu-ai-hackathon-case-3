import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# ── paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data" / "extracted"

# ── load data ──────────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    plates  = pd.read_csv(DATA / "1_1_Export_Plates.csv")
    gaskets = pd.read_csv(DATA / "1_2_Gaskets.csv")
    projects = pd.read_csv(DATA / "1_3_Export_Project_list.csv")

    # normalise factory column name so both sheets share one name
    plates  = plates.rename(columns={"Plate Factory": "Factory",
                                     "Plate Final": "Material Final",
                                     "Plate Description": "Material Description Full"})
    gaskets = gaskets.rename(columns={"Gasket Factory": "Factory",
                                      "Gasket Final": "Material Final",
                                      "Gasket Description": "Material Description Full"})
    plates["Product Type"]  = "Plates"
    gaskets["Product Type"] = "Gaskets"

    demand = pd.concat([plates, gaskets], ignore_index=True)

    # clean factory → short plant code  e.g. "P01_NW02_Northwind Heartland" → "NW02 – Northwind Heartland"
    demand["Plant"] = demand["Factory"].str.extract(r"P01_(NW\d+)_(.+)").apply(
        lambda r: f"{r[0]} – {r[1]}" if pd.notna(r[0]) else "Unknown", axis=1
    )

    # monthly demand columns
    month_cols = [c for c in demand.columns if str(c)[0].isdigit()]

    # merge project metadata in
    demand = demand.merge(
        projects[["Project name", "Region", "Customer segment", "Revenue tier",
                  "Submission channel", "Probability", "Owner",
                  "Requested delivery date", "Total expected EUR", "Total expected PCS"]],
        left_on="Project_name", right_on="Project name", how="left"
    )

    projects["Requested delivery date"] = pd.to_datetime(
        projects["Requested delivery date"], errors="coerce"
    )

    return demand, projects, month_cols


demand_df, projects_df, MONTH_COLS = load_data()

# ── page config ────────────────────────────────────────────────────────────
st.set_page_config(page_title="Northwind Sales Pipeline", layout="wide")
st.title("Northwind Sales Pipeline Dashboard")
st.caption("Data from sheets 1_1 · 1_2 · 1_3  |  Jan 2026 – Dec 2028")

# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR – FILTERS
# ══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("Filters")

    # ── Product type ──────────────────────────────────────
    st.subheader("Product Type")
    all_types = ["Plates", "Gaskets"]
    sel_types = [t for t in all_types if st.checkbox(t, value=True, key=f"pt_{t}")]

    st.divider()

    # ── Region ────────────────────────────────────────────
    st.subheader("Region")
    regions = sorted(demand_df["Region"].dropna().unique())
    sel_regions = [r for r in regions if st.checkbox(r, value=True, key=f"reg_{r}")]

    st.divider()

    # ── Customer segment ──────────────────────────────────
    st.subheader("Customer Segment")
    segments = sorted(demand_df["Customer segment"].dropna().unique())
    sel_segments = [s for s in segments if st.checkbox(s, value=True, key=f"seg_{s}")]

    st.divider()

    # ── Revenue tier ──────────────────────────────────────
    st.subheader("Revenue Tier")
    tiers_order = ["Strategic", "Large", "Medium", "Small"]
    tiers = [t for t in tiers_order if t in demand_df["Revenue tier"].dropna().unique()]
    sel_tiers = [t for t in tiers if st.checkbox(t, value=True, key=f"tier_{t}")]

    st.divider()

    # ── Probability ───────────────────────────────────────
    st.subheader("Probability (%)")
    probs = sorted(demand_df["Probability"].dropna().unique().astype(int))
    sel_probs = [p for p in probs if st.checkbox(f"{p}%", value=True, key=f"prob_{p}")]

    st.divider()

    # ── Submission channel ────────────────────────────────
    st.subheader("Submission Channel")
    channels = sorted(demand_df["Submission channel"].dropna().unique())
    sel_channels = [c for c in channels if st.checkbox(c, value=True, key=f"ch_{c}")]

    st.divider()

    # ── Owner ─────────────────────────────────────────────
    st.subheader("Account Owner")
    owners = sorted(demand_df["Owner"].dropna().unique())
    sel_owners = [o for o in owners if st.checkbox(o, value=True, key=f"own_{o}")]

    st.divider()

    # ── Factory / Plant ───────────────────────────────────
    st.subheader("Factory / Plant")
    plants = sorted(demand_df["Plant"].dropna().unique())
    sel_plants = [p for p in plants if st.checkbox(p, value=True, key=f"plant_{p}")]

    st.divider()

    # ── Work Center ───────────────────────────────────────
    st.subheader("Work Center")
    wcs = sorted(demand_df["Work center"].dropna().unique())
    sel_wcs = [w for w in wcs
               if w != "Missing WC"
               and st.checkbox(w, value=True, key=f"wc_{w}")]
    include_missing_wc = st.checkbox("Include 'Missing WC'", value=False, key="wc_missing")
    if include_missing_wc:
        sel_wcs.append("Missing WC")

    st.divider()

    # ── Delivery date range ───────────────────────────────
    st.subheader("Requested Delivery Date")
    min_date = projects_df["Requested delivery date"].min()
    max_date = projects_df["Requested delivery date"].max()
    date_range = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        key="date_range"
    )

    st.divider()
    search_btn = st.button("Search / Analyze", type="primary", use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════
# FILTER THE DATA
# ══════════════════════════════════════════════════════════════════════════
def apply_filters(df):
    mask = pd.Series(True, index=df.index)

    if sel_types:
        mask &= df["Product Type"].isin(sel_types)
    if sel_regions:
        mask &= df["Region"].isin(sel_regions) | df["Region"].isna()
    if sel_segments:
        mask &= df["Customer segment"].isin(sel_segments) | df["Customer segment"].isna()
    if sel_tiers:
        mask &= df["Revenue tier"].isin(sel_tiers) | df["Revenue tier"].isna()
    if sel_probs:
        mask &= df["Probability"].isin(sel_probs) | df["Probability"].isna()
    if sel_channels:
        mask &= df["Submission channel"].isin(sel_channels) | df["Submission channel"].isna()
    if sel_owners:
        mask &= df["Owner"].isin(sel_owners) | df["Owner"].isna()
    if sel_plants:
        mask &= df["Plant"].isin(sel_plants)
    if sel_wcs:
        mask &= df["Work center"].isin(sel_wcs)

    # delivery date filter on project level then merge back
    if len(date_range) == 2:
        start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
        mask &= (
            df["Requested delivery date"].isna() |
            (pd.to_datetime(df["Requested delivery date"], errors="coerce")
             .between(start, end))
        )

    return df[mask].copy()

filtered = apply_filters(demand_df)

# ══════════════════════════════════════════════════════════════════════════
# SEARCH BUTTON → print to terminal
# ══════════════════════════════════════════════════════════════════════════
if search_btn:
    selection_summary = {
        "filters_applied": {
            "product_types": sel_types,
            "regions": sel_regions,
            "segments": sel_segments,
            "revenue_tiers": sel_tiers,
            "probabilities": sel_probs,
            "channels": sel_channels,
            "owners": sel_owners,
            "plants": sel_plants,
            "work_centers": sel_wcs,
            "delivery_date_range": [str(d) for d in date_range] if len(date_range) == 2 else [],
        },
        "result_summary": {
            "total_rows": len(filtered),
            "unique_projects": filtered["Project_name"].nunique(),
            "total_pcs_all_months": float(filtered[MONTH_COLS].sum().sum()),
            "total_eur_pipeline": float(filtered["Total expected EUR"].sum()),
            "product_type_split": filtered["Product Type"].value_counts().to_dict(),
            "region_split": filtered["Region"].value_counts().to_dict(),
            "segment_split": filtered["Customer segment"].value_counts().to_dict(),
        },
        "projects": filtered[
            ["Project_name", "Product Type", "Region", "Customer segment",
             "Revenue tier", "Probability", "Owner", "Submission channel",
             "Plant", "Work center", "Total expected EUR", "Total expected PCS",
             "Requested delivery date"]
        ].drop_duplicates(subset=["Project_name", "Product Type"]).to_dict(orient="records"),
    }

    print("\n" + "="*70, flush=True)
    print("SALES PIPELINE SELECTION", flush=True)
    print("="*70, flush=True)
    print(json.dumps(selection_summary, indent=2, default=str), flush=True)
    print("="*70 + "\n", flush=True)

    st.success(f"Selection printed to terminal — {len(filtered)} rows · {filtered['Project_name'].nunique()} projects")

# ══════════════════════════════════════════════════════════════════════════
# KPI CARDS
# ══════════════════════════════════════════════════════════════════════════
st.subheader("Pipeline Overview")

total_pcs    = filtered[MONTH_COLS].sum().sum()
total_eur    = filtered["Total expected EUR"].sum()
total_proj   = filtered["Project_name"].nunique()
avg_prob     = filtered["Probability"].mean()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Projects",        f"{total_proj:,}")
k2.metric("Pipeline (EUR)",  f"€{total_eur/1e6:.1f}M")
k3.metric("Total PCS",       f"{total_pcs:,.0f}")
k4.metric("Avg Probability", f"{avg_prob:.0f}%" if pd.notna(avg_prob) else "—")

st.divider()

# ══════════════════════════════════════════════════════════════════════════
# CHARTS
# ══════════════════════════════════════════════════════════════════════════

# ── Row 1: Monthly demand + Revenue by segment ────────────────────────────
col1, col2 = st.columns([3, 2])

with col1:
    st.subheader("Monthly Demand (PCS)")
    monthly = filtered[MONTH_COLS].sum()
    monthly.index = pd.to_datetime(
        [f"20{c.split()[1][-2:]}-{int(c.split()[0]):02d}-01"
         if len(c.split()[1]) == 2
         else f"{c.split()[1]}-{int(c.split()[0]):02d}-01"
         for c in monthly.index]
    )
    monthly = monthly.sort_index()
    fig_monthly = px.bar(
        x=monthly.index, y=monthly.values,
        labels={"x": "Month", "y": "PCS"},
        color_discrete_sequence=["#1f77b4"]
    )
    fig_monthly.update_layout(margin=dict(t=10, b=10), height=320)
    st.plotly_chart(fig_monthly, use_container_width=True)

with col2:
    st.subheader("Revenue by Segment (EUR)")
    seg_data = (
        filtered.drop_duplicates(subset=["Project_name", "Product Type"])
        .groupby("Customer segment")["Total expected EUR"]
        .sum().sort_values().reset_index()
    )
    fig_seg = px.bar(
        seg_data, x="Total expected EUR", y="Customer segment",
        orientation="h",
        labels={"Total expected EUR": "EUR", "Customer segment": ""},
        color_discrete_sequence=["#2ca02c"]
    )
    fig_seg.update_layout(margin=dict(t=10, b=10), height=320)
    st.plotly_chart(fig_seg, use_container_width=True)

# ── Row 2: Probability funnel + Region split ──────────────────────────────
col3, col4 = st.columns(2)

with col3:
    st.subheader("Pipeline by Probability Stage")
    prob_data = (
        filtered.drop_duplicates(subset=["Project_name", "Product Type"])
        .groupby("Probability")["Total expected EUR"]
        .sum().reset_index()
        .sort_values("Probability")
    )
    prob_data["Probability"] = prob_data["Probability"].astype(str) + "%"
    fig_prob = px.bar(
        prob_data, x="Probability", y="Total expected EUR",
        labels={"Total expected EUR": "EUR", "Probability": "Win Probability"},
        color="Total expected EUR",
        color_continuous_scale="Blues"
    )
    fig_prob.update_layout(margin=dict(t=10, b=10), height=300, coloraxis_showscale=False)
    st.plotly_chart(fig_prob, use_container_width=True)

with col4:
    st.subheader("Pipeline by Region")
    reg_data = (
        filtered.drop_duplicates(subset=["Project_name", "Product Type"])
        .groupby("Region")["Total expected EUR"]
        .sum().reset_index()
    )
    fig_reg = px.pie(
        reg_data, values="Total expected EUR", names="Region",
        color_discrete_sequence=px.colors.qualitative.Set2
    )
    fig_reg.update_layout(margin=dict(t=10, b=10), height=300)
    st.plotly_chart(fig_reg, use_container_width=True)

# ── Row 3: Product type + Channel split ──────────────────────────────────
col5, col6 = st.columns(2)

with col5:
    st.subheader("Plates vs Gaskets (PCS)")
    type_data = filtered.groupby("Product Type")[MONTH_COLS].sum().sum(axis=1).reset_index()
    type_data.columns = ["Product Type", "Total PCS"]
    fig_type = px.pie(
        type_data, values="Total PCS", names="Product Type",
        color_discrete_sequence=["#1f77b4", "#ff7f0e"]
    )
    fig_type.update_layout(margin=dict(t=10, b=10), height=280)
    st.plotly_chart(fig_type, use_container_width=True)

with col6:
    st.subheader("Revenue by Submission Channel")
    ch_data = (
        filtered.drop_duplicates(subset=["Project_name", "Product Type"])
        .groupby("Submission channel")["Total expected EUR"]
        .sum().reset_index()
    )
    fig_ch = px.bar(
        ch_data, x="Submission channel", y="Total expected EUR",
        labels={"Total expected EUR": "EUR", "Submission channel": ""},
        color_discrete_sequence=["#9467bd"]
    )
    fig_ch.update_layout(margin=dict(t=10, b=10), height=280)
    st.plotly_chart(fig_ch, use_container_width=True)

st.divider()

# ══════════════════════════════════════════════════════════════════════════
# PROJECTS TABLE
# ══════════════════════════════════════════════════════════════════════════
st.subheader(f"Projects Table  ({filtered['Project_name'].nunique()} projects, {len(filtered)} rows)")

table_cols = [
    "Project_name", "Product Type", "Region", "Customer segment",
    "Revenue tier", "Probability", "Owner", "Submission channel",
    "Plant", "Work center", "Total expected EUR", "Total expected PCS",
    "Requested delivery date"
]

table_df = (
    filtered[table_cols]
    .rename(columns={"Project_name": "Project"})
    .sort_values(["Total expected EUR"], ascending=False)
)

st.dataframe(table_df, use_container_width=True, hide_index=True)

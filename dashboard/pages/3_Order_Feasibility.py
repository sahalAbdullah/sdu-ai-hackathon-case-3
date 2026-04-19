"""
Order Feasibility — same selection UI as Customer Request,
but on Submit runs run.py and renders results beautifully.
"""

import json, sys
from pathlib import Path
from datetime import date, timedelta, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data" / "extracted"
RUN  = Path(__file__).parent / "run.py"

st.set_page_config(page_title="Order Feasibility", layout="wide")

# ── import run.py functions directly ──────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from run_data_pipeline import check_material, _csv_for_type, _parse_factory_code, FACTORY_NAMES

# ── load reference data ────────────────────────────────────────────────────
@st.cache_data
def load_reference():
    p1 = pd.read_csv(DATA / "1_1_Export_Plates.csv")
    p2 = pd.read_csv(DATA / "1_2_Gaskets.csv")
    p3 = pd.read_csv(DATA / "1_3_Export_Project_list.csv")

    p1["family"]    = p1["Material Description"].str.extract(r"^([A-Z0-9\-]+)/")
    p1["thickness"] = p1["Material Description"].str.extract(r"(\d+\.\d+mm)")
    p2["gasket_size"]  = p2["Material Description"].str.extract(r"^(M\d+)-")
    p2["rubber_type"]  = p2["Material Description"].str.extract(r"M\d+-([A-Z]+)\s")

    plate_families  = sorted(p1["family"].dropna().unique())
    plate_thickness = sorted(p1["thickness"].dropna().unique())
    gasket_sizes    = sorted(p2["gasket_size"].dropna().unique(), key=lambda x: int(x[1:]))
    rubber_types    = sorted(p2["rubber_type"].dropna().unique())
    segments        = sorted(p3["Customer segment"].dropna().unique())
    plants          = sorted(
        p1["Plate Factory"].dropna().str.extract(r"P01_(NW\d+)_(.+)")
        .apply(lambda r: f"{r[0]} – {r[1]}", axis=1).unique()
    )
    regions = sorted(p3["Region"].dropna().unique())
    return plate_families, plate_thickness, gasket_sizes, rubber_types, segments, plants, regions, p1, p2

plate_families, plate_thickness, gasket_sizes, rubber_types, \
    segments, plants, regions, plates_df, gaskets_df = load_reference()

# ══════════════════════════════════════════════════════════════════════════
# PAGE HEADER
# ══════════════════════════════════════════════════════════════════════════
st.title("Order Feasibility Check")
st.caption("Select materials → Submit → instant feasibility report across all factories")

def clear_all():
    for k in list(st.session_state.keys()):
        del st.session_state[k]

st.divider()

# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — PRODUCT TYPE
# ══════════════════════════════════════════════════════════════════════════
st.subheader("Step 1 — What product do you need?")
product_type = st.radio(
    "Product type",
    ["Plates", "Gaskets", "Both (Plates + Gaskets)"],
    horizontal=True, label_visibility="collapsed"
)
need_plates  = product_type in ["Plates", "Both (Plates + Gaskets)"]
need_gaskets = product_type in ["Gaskets", "Both (Plates + Gaskets)"]
st.divider()

# ══════════════════════════════════════════════════════════════════════════
# STEP 2a — PLATES
# ══════════════════════════════════════════════════════════════════════════
plate_selection = []
if need_plates:
    st.subheader("Step 2a — Plate Specifications")
    st.caption("Select model family and thickness, then pick specific materials.")
    col_fam, col_thick = st.columns(2)
    with col_fam:
        st.markdown("**Model Family**")
        sel_families = [f for f in plate_families if st.checkbox(f, key=f"fam_{f}")]
    with col_thick:
        st.markdown("**Thickness**")
        sel_thickness = [t for t in plate_thickness if st.checkbox(t, key=f"thick_{t}")]

    if sel_families or sel_thickness:
        mask = pd.Series(True, index=plates_df.index)
        if sel_families:  mask &= plates_df["family"].isin(sel_families)
        if sel_thickness: mask &= plates_df["thickness"].isin(sel_thickness)
        matched = (plates_df[mask][["Material number","Material Description"]]
                   .dropna().drop_duplicates().reset_index(drop=True))
        if not matched.empty:
            st.markdown(f"**{len(matched)} matching plate materials — pick the ones you need:**")
            options = [f"{r['Material number']} — {r['Material Description']}" for _, r in matched.iterrows()]
            picked  = st.multiselect("Select plate materials", options=options,
                                     placeholder="Choose one or more materials...", key="plate_pick")
            if picked:
                plate_qty = st.number_input("Quantity per material (PCS)", min_value=1, value=500, step=100, key="plate_qty")
                for item in picked:
                    mat_num, desc = item.split(" — ", 1)
                    plate_selection.append({"material_number": mat_num.strip(), "description": desc.strip(),
                                            "type": "Plate", "quantity_pcs": plate_qty})
            else:
                st.info("Pick at least one material from the list above.")
        else:
            st.info("No plates match that combination — try different filters.")
    else:
        st.info("Select at least one model family or thickness above.")
    st.divider()

# ══════════════════════════════════════════════════════════════════════════
# STEP 2b — GASKETS
# ══════════════════════════════════════════════════════════════════════════
gasket_selection = []
if need_gaskets:
    st.subheader("Step 2b — Gasket Specifications")
    st.caption("Select gasket sizes and rubber material types.")
    col_sz, col_rub = st.columns(2)
    with col_sz:
        st.markdown("**Gasket Size**")
        sel_sizes = [s for s in gasket_sizes if st.checkbox(s, key=f"gsz_{s}")]
    with col_rub:
        st.markdown("**Rubber / Sealing Material**")
        rubber_labels = {
            "HNBR": "HNBR — Hydrogenated Nitrile (heat + oil resistant)",
            "NBR":  "NBR  — Nitrile Butadiene (standard oil resistant)",
            "FKM":  "FKM  — Fluorocarbon (high temp / chemicals)",
            "EPDM": "EPDM — Ethylene Propylene (steam / hot water)",
            "SILI": "SILI — Silicone (food grade / high temp)",
            "BFG":  "BFG  — Blue Food Grade (FDA / food & pharma)",
            "AFLAS":"AFLAS— Tetrafluoroethylene (aggressive chemicals)",
        }
        sel_rubbers = [r for r in rubber_types if st.checkbox(rubber_labels.get(r, r), key=f"grub_{r}")]

    if sel_sizes or sel_rubbers:
        mask = pd.Series(True, index=gaskets_df.index)
        if sel_sizes:   mask &= gaskets_df["gasket_size"].isin(sel_sizes)
        if sel_rubbers: mask &= gaskets_df["rubber_type"].isin(sel_rubbers)
        matched_g = (gaskets_df[mask][["Material number","Material Description"]]
                     .dropna().drop_duplicates().reset_index(drop=True))
        if not matched_g.empty:
            st.markdown(f"**{len(matched_g)} matching gasket materials — pick the ones you need:**")
            options_g = [f"{r['Material number']} — {r['Material Description']}" for _, r in matched_g.iterrows()]
            picked_g  = st.multiselect("Select gasket materials", options=options_g,
                                       placeholder="Choose one or more materials...", key="gasket_pick")
            if picked_g:
                gasket_qty = st.number_input("Quantity per material (PCS)", min_value=1, value=500, step=100, key="gasket_qty")
                for item in picked_g:
                    mat_num, desc = item.split(" — ", 1)
                    gasket_selection.append({"material_number": mat_num.strip(), "description": desc.strip(),
                                             "type": "Gasket", "quantity_pcs": gasket_qty})
            else:
                st.info("Pick at least one gasket from the list above.")
        else:
            st.info("No gaskets match that combination — try different filters.")
    else:
        st.info("Select at least one size or rubber type above.")
    st.divider()

# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — CUSTOMER DETAILS
# ══════════════════════════════════════════════════════════════════════════
st.subheader("Step 3 — Customer Details")
col_a, col_b, col_c = st.columns(3)
with col_a:
    customer_name    = st.text_input("Customer / Company name", placeholder="e.g. Arctic Cooling GmbH")
    customer_segment = st.selectbox("Industry / Segment", options=["— select —"] + segments)
    customer_region  = st.selectbox("Region", options=["— select —"] + regions)
with col_b:
    delivery_date   = st.date_input("Requested delivery date",
                                    value=date.today() + timedelta(days=90), min_value=date.today())
    preferred_plant = st.selectbox("Preferred factory (optional)", options=["No preference"] + plants)
with col_c:
    priority = st.selectbox("Order priority", ["Standard", "Urgent", "Critical"])
    notes    = st.text_area("Additional notes", placeholder="Any special requirements...")
st.divider()

# ══════════════════════════════════════════════════════════════════════════
# SUBMIT / CLEAR
# ══════════════════════════════════════════════════════════════════════════
all_materials = plate_selection + gasket_selection
has_selection = len(all_materials) > 0

if not has_selection:
    st.warning("Select at least one material above before submitting.")

btn_col1, btn_col2 = st.columns([1, 5])
with btn_col1:
    submit_btn = st.button("Check Feasibility", type="primary",
                           disabled=not has_selection, use_container_width=True)
with btn_col2:
    st.button("Clear All Fields", on_click=clear_all, type="secondary")

# ══════════════════════════════════════════════════════════════════════════
# RUN FEASIBILITY
# ══════════════════════════════════════════════════════════════════════════
if submit_btn and has_selection:

    request = {
        "customer": {
            "name":    customer_name or "Unknown",
            "segment": customer_segment if customer_segment != "— select —" else "Not specified",
            "region":  customer_region  if customer_region  != "— select —" else "Not specified",
        },
        "order": {
            "requested_delivery_date": str(delivery_date),
            "preferred_factory":       preferred_plant,
            "priority":                priority,
            "notes":                   notes or "None",
        },
        "materials_requested": all_materials,
        "summary": {
            "total_materials": len(all_materials),
            "total_pcs":       sum(m["quantity_pcs"] for m in all_materials),
            "plates_count":    len(plate_selection),
            "gaskets_count":   len(gasket_selection),
        }
    }

    # save JSON
    json_path = ROOT / "temp_selection.json"
    with open(json_path, "w") as f:
        json.dump(request, f, indent=2)

    # ── full-screen loader ────────────────────────────────────────────────
    loader = st.empty()
    with loader.container():
        st.markdown("""
        <div style="display:flex;flex-direction:column;align-items:center;
                    justify-content:center;height:60vh;gap:24px;">
            <h2 style="color:#1f77b4;margin:0">Checking Feasibility</h2>
            <p style="color:#666;font-size:1.1rem;margin:0">
                Scanning inventory · capacity · lead times across all 15 factories...
            </p>
        </div>
        """, unsafe_allow_html=True)
        spinner_ph = st.spinner("Running analysis...")

    # ── run the algorithm ────────────────────────────────────────────────
    preferred    = _parse_factory_code(preferred_plant.split("–")[0] if "–" in preferred_plant else preferred_plant)
    req_date     = datetime.strptime(str(delivery_date), "%Y-%m-%d").date()
    results      = []

    with spinner_ph:
        for item in all_materials:
            mat_id   = item["material_number"]
            qty      = int(item["quantity_pcs"])
            m_type   = item.get("type", "Plate")
            csv_path = _csv_for_type(m_type)
            if not csv_path.exists():
                results.append({"material": mat_id, "error": f"Dataset not found: {csv_path.name}"})
                continue
            df = pd.read_csv(csv_path)
            results.append(check_material(df, mat_id, qty, preferred, req_date))

    loader.empty()  # remove full-screen loader

    # ══════════════════════════════════════════════════════════════════════
    # RENDER RESULTS
    # ══════════════════════════════════════════════════════════════════════
    today      = date.today()
    days_left  = (req_date - today).days

    # ── header banner ────────────────────────────────────────────────────
    overall_ok  = all(r.get("sol_a", {}) and r["sol_a"]["status"] == "FULL" for r in results if not r.get("error"))
    overall_any = any(r.get("sol_a", {}) and r["sol_a"]["status"] != "NONE" for r in results if not r.get("error"))
    if overall_ok:
        banner_color, banner_icon, banner_text = "#28a745", "✅", "FULLY FEASIBLE ON TIME"
    elif overall_any:
        banner_color, banner_icon, banner_text = "#fd7e14", "⚠️", "PARTIALLY FEASIBLE"
    else:
        banner_color, banner_icon, banner_text = "#dc3545", "❌", "NOT FEASIBLE BY DEADLINE"

    st.markdown(f"""
    <div style="background:{banner_color};color:white;padding:20px 32px;
                border-radius:12px;margin-bottom:24px;">
        <h2 style="margin:0;font-size:1.8rem">{banner_icon} {banner_text}</h2>
        <p style="margin:4px 0 0;opacity:0.9;font-size:1rem">
            Customer: <b>{request['customer']['name']}</b> &nbsp;|&nbsp;
            Deadline: <b>{delivery_date}</b> ({days_left} days from today) &nbsp;|&nbsp;
            Priority: <b>{priority}</b>
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── top KPI row ──────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Materials",    len(all_materials))
    k2.metric("Total PCS",    f"{sum(m['quantity_pcs'] for m in all_materials):,}")
    k3.metric("Days to Deadline", days_left)
    k4.metric("Preferred Factory", preferred if preferred != "No" else "Any")
    st.divider()

    # ── per-material results ─────────────────────────────────────────────
    for res in results:
        if res.get("error"):
            st.error(f"**{res['material']}** — {res['error']}")
            continue

        mat   = res["material"]
        desc  = res["description"]
        qty   = res["qty"]
        sol_a = res["sol_a"]
        sol_b = res["sol_b"]

        with st.expander(f"📦 {desc}  ({mat})  ·  {qty:,} PCS", expanded=True):

            # preferred factory warning
            if not res.get("pref_exists", True):
                avail = ", ".join(r["plant"] for r in res["records"])
                st.warning(f"**{preferred}** does not produce this material. "
                           f"Available at: **{avail}**")

            show_a = sol_a["status"] != "NONE"
            show_b = sol_b is not None
            col_a_col, col_b_col = (
                st.columns(2) if (show_a and show_b) else
                [st.container(), None] if (show_a and not show_b) else
                [None, st.container()]
            )

            # ── SOLUTION A — only show if stock exists ─────────────────────
            if show_a:
                with col_a_col:
                    status_colors = {"FULL": "#28a745", "PARTIAL": "#fd7e14"}
                    status_icons  = {"FULL": "✅", "PARTIAL": "⚠️"}
                    sc = status_colors.get(sol_a["status"], "#fd7e14")
                    si = status_icons.get(sol_a["status"], "⚠️")

                    st.markdown(f"""
                    <div style="background:{sc}22;border-left:4px solid {sc};
                                padding:12px 16px;border-radius:8px;margin-bottom:12px">
                        <b style="color:{sc};font-size:1.05rem">{si} Solution A — Meet the Deadline</b><br>
                        <span style="color:#333;font-size:0.9rem">{sol_a['note']}</span>
                    </div>
                    """, unsafe_allow_html=True)

                    if sol_a["legs"]:
                        rows = []
                        for leg in sol_a["legs"]:
                            risk_color = {"LOW":"🟢","MODERATE":"🟡","CRITICAL":"🔴"}.get(leg["cap_risk"],"⚪")
                            rows.append({
                                "Factory":    leg["name"],
                                "Qty (PCS)":  f"{leg['qty']:,}",
                                "Ships by":   str(leg["delivery"]),
                                "Capacity":   f"{risk_color} {leg['util']*100:.0f}% {leg['cap_risk']}",
                                "Cost (EUR)": f"€{leg['line_cost']:,.0f}",
                            })
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                        st.markdown(f"**Total: {sol_a['covered']:,} PCS · €{sol_a['total_cost']:,.0f} · by {sol_a['last_date']}**")

            # ── SOLUTION B — only shown when Solution A is not FULL ──────
            if show_b:
                with col_b_col:
                    best = sol_b["best"]
                    st.markdown(f"""
                    <div style="background:#1f77b422;border-left:4px solid #1f77b4;
                                padding:12px 16px;border-radius:8px;margin-bottom:12px">
                        <b style="color:#1f77b4;font-size:1.05rem">🏭 Solution B — One Factory, Full Qty</b><br>
                        <span style="color:#333;font-size:0.9rem">Single source for all {qty:,} PCS</span>
                    </div>
                    """, unsafe_allow_html=True)

                    if best:
                        delta   = (req_date - best["delivery"]).days
                        on_time = delta >= 0
                        t_color = "#28a745" if on_time else "#dc3545"
                        t_label = f"+{delta}d buffer" if on_time else f"{abs(delta)}d late"
                        t_icon  = "⬆" if on_time else "⬇"

                        r1, r2, r3 = st.columns(3)
                        r1.markdown(f"""
                        <div style="background:#f8f9fa;padding:10px 12px;border-radius:8px;text-align:center">
                            <div style="font-size:0.75rem;color:#666;margin-bottom:4px">Best Factory</div>
                            <div style="font-size:1rem;font-weight:700">{best['name'].split('(')[0].strip()}</div>
                        </div>""", unsafe_allow_html=True)

                        r2.markdown(f"""
                        <div style="background:#f8f9fa;padding:10px 12px;border-radius:8px;text-align:center">
                            <div style="font-size:0.75rem;color:#666;margin-bottom:4px">Delivery Date</div>
                            <div style="font-size:1rem;font-weight:700">{best['delivery']}</div>
                            <div style="font-size:0.8rem;color:{t_color}">{t_icon} {t_label}</div>
                        </div>""", unsafe_allow_html=True)

                        r3.markdown(f"""
                        <div style="background:#f8f9fa;padding:10px 12px;border-radius:8px;text-align:center">
                            <div style="font-size:0.75rem;color:#666;margin-bottom:4px">Total Cost</div>
                            <div style="font-size:1rem;font-weight:700">€{best['total_cost']:,.0f}</div>
                            <div style="font-size:0.8rem;color:#666">€{best['cost_unit']:.2f}/pc</div>
                        </div>""", unsafe_allow_html=True)

                        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
                        c1, c2 = st.columns(2)
                        c1.markdown(f"""
                        <div style="background:#e8f5e9;padding:10px 12px;border-radius:8px;text-align:center">
                            <div style="font-size:0.75rem;color:#666;margin-bottom:4px">From Stock</div>
                            <div style="font-size:1rem;font-weight:700;color:#28a745">{best['from_stock']:,} PCS</div>
                        </div>""", unsafe_allow_html=True)
                        c2.markdown(f"""
                        <div style="background:#fff3e0;padding:10px 12px;border-radius:8px;text-align:center">
                            <div style="font-size:0.75rem;color:#666;margin-bottom:4px">From Production</div>
                            <div style="font-size:1rem;font-weight:700;color:#fd7e14">{best['from_prod']:,} PCS</div>
                        </div>""", unsafe_allow_html=True)

                        if sol_b["all"]:
                            chart_data = pd.DataFrame([{
                                "Factory": o["name"].split("(")[0].strip(),
                                "Delivery": str(o["delivery"]),
                                "Days to Deliver": (o["delivery"] - today).days,
                                "On Time": "✅ On Time" if o["delivery"] <= req_date else "❌ Late",
                                "Cost EUR": o["total_cost"],
                            } for o in sol_b["all"]])

                            fig = px.bar(
                                chart_data, x="Factory", y="Days to Deliver",
                                color="On Time",
                                color_discrete_map={"✅ On Time": "#28a745", "❌ Late": "#dc3545"},
                                text="Delivery",
                                labels={"Days to Deliver": "Days until delivery"},
                                height=280,
                            )
                            fig.update_traces(textposition="outside")
                            fig.add_hline(y=days_left, line_dash="dash",
                                          line_color="orange",
                                          annotation_text="Deadline",
                                          annotation_position="right")
                            fig.update_layout(margin=dict(t=10, b=10), showlegend=True,
                                              legend=dict(orientation="h", y=-0.3))
                            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.success(f"Analysis complete — {len(results)} material(s) checked across all 15 Northwind factories.")

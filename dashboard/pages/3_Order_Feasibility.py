"""
Order Feasibility — selection UI feeds into the full pipeline.
Ranks all orders (3 hardcoded + new) and runs feasibility in priority order.
"""

import json, sys
from pathlib import Path
from datetime import date, timedelta, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data" / "extracted"

st.set_page_config(page_title="Order Feasibility", layout="wide")

# ── import pipeline ────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from pipeline import (
    run_pipeline, HARDCODED_ORDERS, load_new_order,
    _parse_factory_code, FACTORY_NAMES, TODAY,
)

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
    priority        = st.selectbox("Order priority", ["Standard", "Urgent", "Critical"])
    preferred_plant = st.selectbox("Preferred factory (optional)", options=["No preference"] + plants)
with col_c:
    probability  = st.selectbox("Chances of Order (%)", options=[10, 25, 50, 75, 90], index=2)
    revenue_tier = st.selectbox("Revenue Tier", options=["Strategic", "Large", "Medium", "Small"], index=2)
    notes        = st.text_area("Additional notes", placeholder="Any special requirements...")
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
# RUN PIPELINE
# ══════════════════════════════════════════════════════════════════════════
if submit_btn and has_selection:

    # ── order ID is always slot 4 (3 hardcoded + this new one) ──────────
    order_id = f"ORD-{len(HARDCODED_ORDERS) + 1:03d}"

    new_order = {
        "order_id": order_id,
        "customer": {
            "name":         customer_name or "Unknown",
            "segment":      customer_segment if customer_segment != "— select —" else "Not specified",
            "region":       customer_region  if customer_region  != "— select —" else "Not specified",
            "probability":  probability,
            "revenue_tier": revenue_tier,
        },
        "order": {
            "requested_delivery_date": str(delivery_date),
            "preferred_factory":       preferred_plant,
            "priority":                priority,
            "notes":                   notes or "None",
        },
        "materials_requested": all_materials,
    }

    json_path = ROOT / "temp_selection.json"
    with open(json_path, "w") as f:
        json.dump(new_order, f, indent=2)

    # ── full-screen loader ────────────────────────────────────────────────
    loader = st.empty()
    with loader.container():
        st.markdown("""
        <div style="display:flex;flex-direction:column;align-items:center;
                    justify-content:center;height:60vh;gap:24px;">
            <h2 style="color:#1f77b4;margin:0">🔄 Running Pipeline</h2>
            <p style="color:#666;font-size:1.1rem;margin:0">
                Ranking orders · allocating inventory · checking feasibility across all 15 factories...
            </p>
        </div>
        """, unsafe_allow_html=True)

    with st.spinner("Pipeline running..."):
        all_orders = list(HARDCODED_ORDERS) + [new_order]
        ranked = run_pipeline(all_orders)

    loader.empty()

    today = date.today()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1 — PIPELINE RANKING TABLE
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("""
    <h2 style="margin-bottom:4px">📊 Pipeline Priority Ranking</h2>
    <p style="color:#9aa0ac;margin-top:0">Orders ranked by composite score — Rank 1 gets inventory first</p>
    """, unsafe_allow_html=True)

    tier_colors = {"Strategic": "#7b2ff7", "Large": "#1f77b4", "Medium": "#fd7e14", "Small": "#6c757d"}
    ranking_rows = []
    for r in ranked:
        oid      = r.get("order_id", "?")
        is_new   = oid == order_id
        tag      = " 🆕" if is_new else ""
        n_mats   = len(r.get("materials_requested", []))
        total_pcs = sum(int(m.get("quantity_pcs", 0)) for m in r.get("materials_requested", []))
        ranking_rows.append({
            "Rank":         f"#{r['rank']}",
            "Order ID":     oid + tag,
            "Customer":     r.get("customer", {}).get("name", "?"),
            "Segment":      r.get("customer", {}).get("segment", "?"),
            "Probability":  f"{r['probability']}%",
            "Revenue Tier": r.get("revenue_tier", "?"),
            "Loyalty":      f"{r['hist_orders']} projects",
            "Score":        f"{r['composite_score']:.4f}",
            "Materials":    n_mats,
            "Total PCS":    f"{total_pcs:,}",
            "Deadline":     r["order"].get("requested_delivery_date", "?"),
            "Priority":     r["order"].get("priority", "?"),
        })

    _cols = ["Rank","Order ID","Customer","Segment","Probability",
             "Revenue Tier","Loyalty","Score","Materials","Total PCS","Deadline","Priority"]
    _hdr = "".join(f'<th style="padding:8px 12px;text-align:left;border-bottom:2px solid #dee2e6;white-space:nowrap">{c}</th>' for c in _cols)
    _rows_html = ""
    for _row in ranking_rows:
        _is_new = order_id in _row["Order ID"]
        _bg  = "background:#2a2000;border-left:4px solid #ffc107;" if _is_new else "background:#1a1d24;"
        _fw  = "font-weight:700;color:#ffc107;" if _is_new else "color:#e8eaed;"
        _cells = "".join(f'<td style="padding:9px 14px;{_fw}white-space:nowrap;border-bottom:1px solid #2d3139">{_row[c]}</td>' for c in _cols)
        _rows_html += f'<tr style="{_bg}">{_cells}</tr>'
    _hdr_styled = "".join(
        f'<th style="padding:10px 14px;text-align:left;border-bottom:2px solid #3a3f4b;'
        f'color:#9aa0ac;font-weight:600;font-size:0.8rem;white-space:nowrap;letter-spacing:0.04em">{c}</th>'
        for c in _cols
    )
    st.markdown(f"""
    <div style="overflow-x:auto;border:1px solid #2d3139;border-radius:10px;background:#141720">
    <table style="width:100%;border-collapse:collapse;font-size:0.88rem">
      <thead><tr style="background:#1e2230">{_hdr_styled}</tr></thead>
      <tbody>{_rows_html}</tbody>
    </table>
    </div>
    <p style="font-size:0.78rem;color:#9aa0ac;margin:6px 0 0">
      🟡 Highlighted = your order
    </p>
    """, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1b — PIPELINE CHARTS
    # ══════════════════════════════════════════════════════════════════════
    _TIER = {"Strategic": 1.0, "Large": 0.75, "Medium": 0.5, "Small": 0.25}

    # build per-order chart data
    _cdata = []
    for r in ranked:
        oid   = r.get("order_id", "?")
        label = f"#{r['rank']} {oid}"
        res_list = [x for x in r.get("results", []) if not x.get("error")]

        # score components
        prob_c = (r["probability"] / 100) * 0.50
        tier_c = _TIER.get(r["revenue_tier"], 0.5) * 0.30
        loy_c  = min(r["hist_orders"] / 20, 1.0) * 0.20

        # PCS coverage
        total_qty   = sum(x["qty"] for x in res_list)
        from_stock  = sum(x["sol_a"]["covered"] for x in res_list if x.get("sol_a"))
        from_prod   = sum(
            x["sol_b"]["best"]["from_prod"] for x in res_list
            if x.get("sol_b") and x["sol_b"].get("best")
        )
        uncovered   = max(0, total_qty - from_stock - from_prod)

        # worst-case delivery (latest across materials)
        deliveries = []
        for x in res_list:
            if x.get("sol_a") and x["sol_a"]["status"] == "FULL" and x["sol_a"]["last_date"]:
                deliveries.append(x["sol_a"]["last_date"])
            elif x.get("sol_b") and x["sol_b"].get("best"):
                deliveries.append(x["sol_b"]["best"]["delivery"])
        worst_del = max(deliveries) if deliveries else None
        deadline  = r["req_date"]
        days_to_del  = (worst_del  - today).days if worst_del else 0
        days_to_dead = (deadline   - today).days

        # total fulfillment cost per order
        order_cost = 0
        for x in res_list:
            if x.get("sol_a") and x["sol_a"]["status"] == "FULL":
                order_cost += x["sol_a"]["total_cost"]
            elif x.get("sol_b") and x["sol_b"].get("best"):
                order_cost += x["sol_b"]["best"]["total_cost"]
            elif x.get("sol_a") and x["sol_a"]["total_cost"]:
                order_cost += x["sol_a"]["total_cost"]

        _cdata.append({
            "label": label, "order_id": oid,
            "prob_c": prob_c, "tier_c": tier_c, "loy_c": loy_c,
            "from_stock": from_stock, "from_prod": from_prod, "uncovered": uncovered,
            "days_to_del": days_to_del, "days_to_dead": days_to_dead,
            "on_time": worst_del <= deadline if worst_del else False,
            "customer": r.get("customer", {}).get("name", "?"),
            "order_cost": order_cost,
            "is_new": oid == order_id,
        })

    labels = [d["label"] for d in _cdata]
    bar_colors = ["#ffc107" if d["is_new"] else "#1f77b4" for d in _cdata]

    ch_inv, ch_cost = st.columns(2)

    # ── Chart 1: Inventory Allocation (stacked bar) ───────────────────────
    with ch_inv:
        st.markdown("**📦 Inventory Allocation — Who Gets What?**")
        fig_inv = px.bar(
            pd.DataFrame({
                "Order":  labels * 3,
                "Source": (["From Stock ✅"]      * len(_cdata) +
                           ["From Production 🔧"] * len(_cdata) +
                           ["Uncovered ❌"]        * len(_cdata)),
                "PCS":    ([d["from_stock"] for d in _cdata] +
                           [d["from_prod"]  for d in _cdata] +
                           [d["uncovered"]  for d in _cdata]),
            }),
            x="Order", y="PCS", color="Source", barmode="stack",
            color_discrete_map={
                "From Stock ✅":      "#28a745",
                "From Production 🔧": "#fd7e14",
                "Uncovered ❌":        "#dc3545",
            },
            height=320,
        )
        fig_inv.update_layout(
            margin=dict(t=10, b=10, l=10, r=10),
            legend=dict(orientation="h", y=-0.25, font=dict(size=11)),
            yaxis_title="PCS", xaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_inv, use_container_width=True)

    # ── Chart 2: Fulfillment Cost per Order (bar) ─────────────────────────
    with ch_cost:
        st.markdown("**💶 Fulfillment Cost per Order (EUR)**")
        fig_cost = px.bar(
            pd.DataFrame({
                "Order":    labels,
                "Cost EUR": [d["order_cost"] for d in _cdata],
                "Customer": [d["customer"]   for d in _cdata],
            }),
            x="Order", y="Cost EUR",
            text=[f"€{d['order_cost']:,.0f}" for d in _cdata],
            hover_data=["Customer"],
            height=320,
            color_discrete_sequence=["#1f77b4"],
        )
        fig_cost.update_traces(
            textposition="outside",
            textfont_size=11,
            marker_color=bar_colors,
        )
        fig_cost.update_layout(
            margin=dict(t=10, b=10, l=10, r=10),
            yaxis_title="EUR", xaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )
        st.plotly_chart(fig_cost, use_container_width=True)

    st.caption("🟡 Yellow bar = your new order")
    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 2 — PER-ORDER FEASIBILITY (in rank order)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("""
    <h2 style="margin-bottom:4px;color:#e8eaed">🏭 Feasibility by Rank</h2>
    <p style="color:#9aa0ac;margin-top:0">Inventory depleted in priority order — Rank 1 gets first pick</p>
    """, unsafe_allow_html=True)

    for r in ranked:
        oid      = r.get("order_id", "?")
        cust     = r.get("customer", {})
        is_new   = oid == order_id
        req_date = r["req_date"]
        days_left = (req_date - today).days
        results  = r.get("results", [])

        ok_count   = sum(1 for x in results if x.get("sol_a") and x["sol_a"]["status"] == "FULL")
        part_count = sum(1 for x in results if x.get("sol_a") and x["sol_a"]["status"] == "PARTIAL")
        none_count = sum(1 for x in results if not x.get("sol_a") or x["sol_a"]["status"] == "NONE")

        if none_count == len(results):
            order_color, order_icon = "#dc3545", "❌"
        elif ok_count == len(results):
            order_color, order_icon = "#28a745", "✅"
        else:
            order_color, order_icon = "#fd7e14", "⚠️"

        new_badge = ' &nbsp;<span style="background:#6f42c1;color:white;padding:2px 8px;border-radius:4px;font-size:0.75rem">NEW</span>' if is_new else ""
        expander_label = f"Rank #{r['rank']} · {oid} · {cust.get('name','?')} · {len(results)} material(s)"

        with st.expander(expander_label, expanded=is_new):

            # order header card
            st.markdown(f"""
            <div style="background:#1e2230;border-left:5px solid {order_color};
                        padding:14px 18px;border-radius:8px;margin-bottom:16px">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <div>
                        <span style="font-size:1.2rem;font-weight:700;color:{order_color}">
                            {order_icon} {oid}{new_badge}
                        </span>
                        &nbsp;&nbsp;
                        <span style="color:#b0b8c8;font-size:0.9rem">
                            {cust.get('name','?')} · {cust.get('segment','?')} · {cust.get('region','?')}
                        </span>
                    </div>
                    <div style="text-align:right;font-size:0.85rem;color:#9aa0ac">
                        Score: <b style="color:#e8eaed">{r['composite_score']:.4f}</b> &nbsp;|&nbsp;
                        Prob: <b style="color:#e8eaed">{r['probability']}%</b> &nbsp;|&nbsp;
                        Tier: <b style="color:#e8eaed">{r['revenue_tier']}</b><br>
                        Deadline: <b style="color:#e8eaed">{req_date}</b> ({days_left}d) &nbsp;|&nbsp;
                        Factory: <b style="color:#e8eaed">{r['preferred']}</b> &nbsp;|&nbsp;
                        Priority: <b style="color:#e8eaed">{r['order'].get('priority','?')}</b>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # KPI mini-row
            mk1, mk2, mk3, mk4 = st.columns(4)
            mk1.metric("✅ Fully Covered", ok_count)
            mk2.metric("⚠️ Partial",       part_count)
            mk3.metric("❌ No Stock",      none_count)
            mk4.metric("Days to Deadline", days_left)

            st.markdown("---")

            # per-material results
            for res in results:
                if res.get("error"):
                    st.error(f"**{res['material']}** — {res['error']}")
                    continue

                mat   = res["material"]
                desc  = res["description"]
                qty   = res["qty"]
                sol_a = res["sol_a"]
                sol_b = res["sol_b"]

                with st.container():
                    show_a = sol_a and sol_a["status"] != "NONE"
                    show_b = sol_b is not None

                    col_a_col, col_b_col = (
                        st.columns(2) if (show_a and show_b) else
                        [st.container(), None] if (show_a and not show_b) else
                        [None, st.container()]
                    )

                    if show_a:
                        with col_a_col:
                            sc = {"FULL": "#28a745", "PARTIAL": "#fd7e14"}.get(sol_a["status"], "#fd7e14")
                            si = {"FULL": "✅", "PARTIAL": "⚠️"}.get(sol_a["status"], "⚠️")
                            st.markdown(f"""
                            <div style="background:#1e2230;border-left:4px solid {sc};
                                        padding:10px 14px;border-radius:8px;margin-bottom:10px">
                                <b style="color:{sc}">{si} {desc}</b>
                                <span style="color:#9aa0ac;font-size:0.8rem"> · {mat} · {qty:,} PCS</span><br>
                                <span style="color:#b0b8c8;font-size:0.85rem">{sol_a['note']}</span>
                            </div>""", unsafe_allow_html=True)

                            if sol_a["legs"]:
                                leg_rows = []
                                for leg in sol_a["legs"]:
                                    rc = {"LOW":"🟢","MODERATE":"🟡","CRITICAL":"🔴"}.get(leg["cap_risk"],"⚪")
                                    leg_rows.append({
                                        "Factory":    leg["name"],
                                        "Qty (PCS)":  f"{leg['qty']:,}",
                                        "Ships by":   str(leg["delivery"]),
                                        "Capacity":   f"{rc} {leg['util']*100:.0f}%",
                                        "Cost (EUR)": f"€{leg['line_cost']:,.0f}",
                                    })
                                st.dataframe(pd.DataFrame(leg_rows), use_container_width=True, hide_index=True)
                                st.caption(f"Total: {sol_a['covered']:,} PCS · €{sol_a['total_cost']:,.0f} · by {sol_a['last_date']}")

                    if show_b:
                        with col_b_col:
                            best = sol_b["best"]
                            st.markdown(f"""
                            <div style="background:#1e2230;border-left:4px solid #1f77b4;
                                        padding:10px 14px;border-radius:8px;margin-bottom:10px">
                                <b style="color:#1f77b4">🏭 Solution B — {desc}</b>
                                <span style="color:#9aa0ac;font-size:0.8rem"> · {mat} · {qty:,} PCS</span><br>
                                <span style="color:#b0b8c8;font-size:0.85rem">Single factory, full quantity</span>
                            </div>""", unsafe_allow_html=True)

                            if best:
                                delta   = (req_date - best["delivery"]).days
                                on_time = delta >= 0
                                tc = "#28a745" if on_time else "#dc3545"
                                tl = f"+{delta}d buffer" if on_time else f"{abs(delta)}d late"

                                b1, b2, b3 = st.columns(3)
                                b1.markdown(f"""<div style="background:#252a36;padding:8px 10px;border-radius:6px;text-align:center">
                                    <div style="font-size:0.7rem;color:#9aa0ac">Best Factory</div>
                                    <div style="font-size:0.9rem;font-weight:700;color:#e8eaed">{best['name'].split('(')[0].strip()}</div>
                                </div>""", unsafe_allow_html=True)
                                b2.markdown(f"""<div style="background:#252a36;padding:8px 10px;border-radius:6px;text-align:center">
                                    <div style="font-size:0.7rem;color:#9aa0ac">Delivery</div>
                                    <div style="font-size:0.9rem;font-weight:700;color:#e8eaed">{best['delivery']}</div>
                                    <div style="font-size:0.75rem;color:{tc}">{tl}</div>
                                </div>""", unsafe_allow_html=True)
                                b3.markdown(f"""<div style="background:#252a36;padding:8px 10px;border-radius:6px;text-align:center">
                                    <div style="font-size:0.7rem;color:#9aa0ac">Total Cost</div>
                                    <div style="font-size:0.9rem;font-weight:700;color:#e8eaed">€{best['total_cost']:,.0f}</div>
                                    <div style="font-size:0.75rem;color:#9aa0ac">€{best['cost_unit']:.2f}/pc</div>
                                </div>""", unsafe_allow_html=True)

                    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

    st.divider()
    total_mats = sum(len(r.get("results", [])) for r in ranked)
    st.success(f"Pipeline complete — {len(ranked)} orders ranked · {total_mats} materials checked · inventory allocated in priority order")

"""
Customer Request page — customer picks what material they need,
how much, and when. Pressing Submit prints a clean requirement
summary to the terminal (ready for the agent later).
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data" / "extracted"

st.set_page_config(page_title="Customer Request", layout="wide")

# ── load reference data ────────────────────────────────────────────────────
@st.cache_data
def load_reference():
    p1 = pd.read_csv(DATA / "1_1_Export_Plates.csv")
    p2 = pd.read_csv(DATA / "1_2_Gaskets.csv")
    p3 = pd.read_csv(DATA / "1_3_Export_Project_list.csv")

    # plate model families from description  e.g. "XL520/PL.12 304TL 0.7mm" → "XL520"
    p1["family"] = p1["Material Description"].str.extract(r"^([A-Z0-9\-]+)/")
    p1["thickness"] = p1["Material Description"].str.extract(r"(\d+\.\d+mm)")

    # gasket size + rubber type  e.g. "M14-HNBR GASKET 30" → size=M14, rubber=HNBR
    p2["gasket_size"]   = p2["Material Description"].str.extract(r"^(M\d+)-")
    p2["rubber_type"]   = p2["Material Description"].str.extract(r"M\d+-([A-Z]+)\s")

    plate_families  = sorted(p1["family"].dropna().unique())
    plate_thickness = sorted(p1["thickness"].dropna().unique())
    gasket_sizes    = sorted(p2["gasket_size"].dropna().unique(),
                             key=lambda x: int(x[1:]))
    rubber_types    = sorted(p2["rubber_type"].dropna().unique())
    segments        = sorted(p3["Customer segment"].dropna().unique())
    plants          = sorted(
        p1["Plate Factory"].dropna().str.extract(r"P01_(NW\d+)_(.+)")
        .apply(lambda r: f"{r[0]} – {r[1]}", axis=1).unique()
    )
    regions         = sorted(p3["Region"].dropna().unique())

    return plate_families, plate_thickness, gasket_sizes, rubber_types, segments, plants, regions, p1, p2

plate_families, plate_thickness, gasket_sizes, rubber_types, \
    segments, plants, regions, plates_df, gaskets_df = load_reference()

# ══════════════════════════════════════════════════════════════════════════
# PAGE HEADER
# ══════════════════════════════════════════════════════════════════════════
st.title("Customer Material Request")
st.caption("Select what you need → press **Submit Request** → requirement prints to terminal")

# ── clear helper (defined early, used in two places) ──────────────────────
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
    horizontal=True,
    label_visibility="collapsed"
)
need_plates  = product_type in ["Plates", "Both (Plates + Gaskets)"]
need_gaskets = product_type in ["Gaskets", "Both (Plates + Gaskets)"]

st.divider()

# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — PLATE REQUIREMENTS
# ══════════════════════════════════════════════════════════════════════════
plate_selection = []

if need_plates:
    st.subheader("Step 2a — Plate Specifications")
    st.caption("Select one or more model families and thicknesses. Then enter the quantity.")

    col_fam, col_thick = st.columns(2)

    with col_fam:
        st.markdown("**Model Family**")
        sel_families = [f for f in plate_families
                        if st.checkbox(f, key=f"fam_{f}")]

    with col_thick:
        st.markdown("**Thickness**")
        sel_thickness = [t for t in plate_thickness
                         if st.checkbox(t, key=f"thick_{t}")]

    # show matching materials
    if sel_families or sel_thickness:
        mask = pd.Series(True, index=plates_df.index)
        if sel_families:
            mask &= plates_df["family"].isin(sel_families)
        if sel_thickness:
            mask &= plates_df["thickness"].isin(sel_thickness)

        matched = (
            plates_df[mask][["Material number", "Material Description"]]
            .dropna()
            .drop_duplicates()
            .reset_index(drop=True)
        )

        if not matched.empty:
            st.markdown(f"**{len(matched)} matching plate materials — pick the ones you need:**")

            options = [
                f"{row['Material number']} — {row['Material Description']}"
                for _, row in matched.iterrows()
            ]
            picked = st.multiselect(
                "Select plate materials",
                options=options,
                placeholder="Choose one or more materials...",
                key="plate_pick"
            )

            if picked:
                plate_qty = st.number_input(
                    "Quantity per material (PCS)",
                    min_value=1, value=500, step=100,
                    key="plate_qty"
                )
                for item in picked:
                    mat_num, desc = item.split(" — ", 1)
                    plate_selection.append({
                        "material_number": mat_num.strip(),
                        "description": desc.strip(),
                        "type": "Plate",
                        "quantity_pcs": plate_qty
                    })
            else:
                st.info("Pick at least one material from the list above.")
        else:
            st.info("No plates match that combination — try different filters.")
    else:
        st.info("Select at least one model family or thickness above.")

    st.divider()

# ══════════════════════════════════════════════════════════════════════════
# STEP 2b — GASKET REQUIREMENTS
# ══════════════════════════════════════════════════════════════════════════
gasket_selection = []

if need_gaskets:
    st.subheader("Step 2b — Gasket Specifications")
    st.caption("Select gasket sizes and rubber material types.")

    col_sz, col_rub = st.columns(2)

    with col_sz:
        st.markdown("**Gasket Size**")
        sel_sizes = [s for s in gasket_sizes
                     if st.checkbox(s, key=f"gsz_{s}")]

    with col_rub:
        st.markdown("**Rubber / Sealing Material**")
        rubber_labels = {
            "HNBR":  "HNBR — Hydrogenated Nitrile (heat + oil resistant)",
            "NBR":   "NBR  — Nitrile Butadiene (standard oil resistant)",
            "FKM":   "FKM  — Fluorocarbon (high temp / chemicals)",
            "EPDM":  "EPDM — Ethylene Propylene (steam / hot water)",
            "SILI":  "SILI — Silicone (food grade / high temp)",
            "BFG":   "BFG  — Blue Food Grade (FDA / food & pharma)",
            "AFLAS": "AFLAS— Tetrafluoroethylene (aggressive chemicals)",
        }
        sel_rubbers = [r for r in rubber_types
                       if st.checkbox(rubber_labels.get(r, r), key=f"grub_{r}")]

    if sel_sizes or sel_rubbers:
        mask = pd.Series(True, index=gaskets_df.index)
        if sel_sizes:
            mask &= gaskets_df["gasket_size"].isin(sel_sizes)
        if sel_rubbers:
            mask &= gaskets_df["rubber_type"].isin(sel_rubbers)

        matched_g = (
            gaskets_df[mask][["Material number", "Material Description"]]
            .dropna()
            .drop_duplicates()
            .reset_index(drop=True)
        )

        if not matched_g.empty:
            st.markdown(f"**{len(matched_g)} matching gasket materials — pick the ones you need:**")

            options_g = [
                f"{row['Material number']} — {row['Material Description']}"
                for _, row in matched_g.iterrows()
            ]
            picked_g = st.multiselect(
                "Select gasket materials",
                options=options_g,
                placeholder="Choose one or more materials...",
                key="gasket_pick"
            )

            if picked_g:
                gasket_qty = st.number_input(
                    "Quantity per material (PCS)",
                    min_value=1, value=500, step=100,
                    key="gasket_qty"
                )
                for item in picked_g:
                    mat_num, desc = item.split(" — ", 1)
                    gasket_selection.append({
                        "material_number": mat_num.strip(),
                        "description": desc.strip(),
                        "type": "Gasket",
                        "quantity_pcs": gasket_qty
                    })
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
    customer_name = st.text_input("Customer / Company name", placeholder="e.g. Arctic Cooling GmbH")
    customer_segment = st.selectbox("Industry / Segment", options=["— select —"] + segments)
    customer_region = st.selectbox("Region", options=["— select —"] + regions)

with col_b:
    delivery_date = st.date_input(
        "Requested delivery date",
        value=date.today() + timedelta(days=90),
        min_value=date.today(),
    )
    preferred_plant = st.selectbox(
        "Preferred factory (optional)",
        options=["No preference"] + plants
    )

with col_c:
    priority = st.selectbox(
        "Order priority",
        ["Standard", "Urgent", "Critical"]
    )
    notes = st.text_area("Additional notes", placeholder="Any special requirements, certifications, or context...")

st.divider()

# ══════════════════════════════════════════════════════════════════════════
# SUBMIT
# ══════════════════════════════════════════════════════════════════════════
all_materials = plate_selection + gasket_selection
has_selection = len(all_materials) > 0

if not has_selection:
    st.warning("Select at least one material above before submitting.")

btn_col1, btn_col2 = st.columns([1, 5])
with btn_col1:
    submit_btn = st.button(
        "Submit Request",
        type="primary",
        disabled=not has_selection,
        use_container_width=True
    )
with btn_col2:
    st.button("Clear All Fields", on_click=clear_all, type="secondary")

CLAUDE_BIN  = r"C:\Users\sahal\.local\bin\claude.exe"
BASH_BIN    = r"C:\Program Files\Git\bin\bash.exe"

# ── guard: only run once per button press using session state ─────────────
if submit_btn and has_selection and "last_run_id" not in st.session_state:
    st.session_state["last_run_id"] = id(all_materials)

if "last_run_id" in st.session_state and submit_btn and has_selection:

    request = {
        "customer": {
            "name": customer_name or "Unknown",
            "segment": customer_segment if customer_segment != "— select —" else "Not specified",
            "region": customer_region if customer_region != "— select —" else "Not specified",
        },
        "order": {
            "requested_delivery_date": str(delivery_date),
            "preferred_factory": preferred_plant,
            "priority": priority,
            "notes": notes or "None",
        },
        "materials_requested": all_materials,
        "summary": {
            "total_materials": len(all_materials),
            "total_pcs": sum(m["quantity_pcs"] for m in all_materials),
            "plates_count": len(plate_selection),
            "gaskets_count": len(gasket_selection),
        }
    }

    # ── overwrite temp_selection.json — Claude always gets fresh input ───
    json_path = ROOT / "temp_selection.json"
    with open(json_path, "w") as f:
        json.dump(request, f, indent=2)

    # ── print once to terminal ────────────────────────────────────────────
    print("\n" + "="*70, flush=True)
    print("CUSTOMER MATERIAL REQUEST", flush=True)
    print("="*70, flush=True)
    print(f"Customer  : {request['customer']['name']}", flush=True)
    print(f"Segment   : {request['customer']['segment']}", flush=True)
    print(f"Region    : {request['customer']['region']}", flush=True)
    print(f"Delivery  : {request['order']['requested_delivery_date']}", flush=True)
    print(f"Factory   : {request['order']['preferred_factory']}", flush=True)
    print(f"Priority  : {request['order']['priority']}", flush=True)
    print(f"Notes     : {request['order']['notes']}", flush=True)
    print(flush=True)
    print(f"Materials ({len(all_materials)} items, {request['summary']['total_pcs']:,} PCS):", flush=True)
    for m in all_materials:
        print(f"  [{m['type']:6}]  {m['material_number']}  |  {m['description']}  |  {m['quantity_pcs']:,} PCS", flush=True)
    print(f"\nSaved → {json_path}", flush=True)
    print("Triggering Claude agents...\n", flush=True)

    # ── KPI row ───────────────────────────────────────────────────────────
    st.subheader("Request Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Materials",   len(all_materials))
    c2.metric("Total PCS",   f"{request['summary']['total_pcs']:,}")
    c3.metric("Plates",      request['summary']['plates_count'])
    c4.metric("Gaskets",     request['summary']['gaskets_count'])
    st.divider()

    # ── run feasibility algorithm (run.py) ───────────────────────────────
    st.subheader("Feasibility Check")
    st.caption("Algorithm scanning inventory, capacity, and lead times across all factories")

    with st.spinner("Running feasibility check..."):
        run_proc = subprocess.run(
            [sys.executable, str(ROOT / "dashboard" / "pages" / "run.py")],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(ROOT)
        )
        st.code(run_proc.stdout, language="text")

    st.divider()

    # ── stream run_analysis.py output live ───────────────────────────────
    st.subheader("Agent Analysis")

    status_box = st.empty()   # live phase/progress lines
    brief_box  = st.empty()   # final sales brief

    progress_lines = []
    brief_lines    = []
    in_brief       = False    # flip to True once we hit the sales brief header

    with st.spinner("Agents running — please wait..."):
        try:
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "run_analysis.py")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT),
                bufsize=1,
            )

            for line in proc.stdout:
                stripped = line.rstrip()

                # detect start of the final sales brief
                if "SALES BRIEF" in stripped or "━" in stripped:
                    in_brief = True

                if in_brief:
                    brief_lines.append(stripped)
                    brief_box.markdown("\n".join(brief_lines))
                else:
                    progress_lines.append(stripped)
                    status_box.code("\n".join(progress_lines), language="text")

            proc.wait()

            if proc.returncode == 0:
                st.success("Analysis complete!")
            else:
                st.error(f"Agent script exited with code {proc.returncode}")

        except FileNotFoundError:
            st.error("run_analysis.py not found — check scripts/ folder")

    del st.session_state["last_run_id"]


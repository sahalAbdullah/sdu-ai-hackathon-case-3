"""
pipeline.py  --  Priority Ranking + Feasibility Engine

Step 1: Score & rank all orders (probability + revenue tier + customer loyalty)
Step 2: Run feasibility in rank order — Rank 1 consumes inventory first

Self-contained: all feasibility logic is inline (no external run.py needed).
Triggered from Streamlit (3_Order_Feasibility.py) or CLI.
"""

import json
import math
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent.parent
DATA_DIR = ROOT / "data" / "Dataset-Parsed"
EXT_DIR  = ROOT / "data" / "extracted"
SEL_FILE = ROOT / "temp_selection.json"
TODAY    = datetime.today().date()

# ══════════════════════════════════════════════════════════════════════════════
# FEASIBILITY ENGINE  (self-contained — no imports from run.py)
# ══════════════════════════════════════════════════════════════════════════════

FACTORY_NAMES = {
    "NW01": "Northwind Central",   "NW02": "Northwind West",
    "NW03": "Northwind North",     "NW04": "Northwind East",
    "NW05": "Northwind South",     "NW06": "Northwind Southeast",
    "NW07": "Northwind Northwest", "NW08": "Northwind Northeast",
    "NW09": "Northwind Southwest", "NW10": "Northwind Prime",
    "NW11": "Northwind Alpine",    "NW12": "Northwind Atlantic",
    "NW13": "Northwind Pacific",   "NW14": "Northwind Nordic",
    "NW15": "Northwind Baltic",
}

UTIL_WARN      = 0.80
UTIL_CRITICAL  = 0.95
TRANSPORT_DAYS = 45
MIN_OPS_PER_WEEK = 1.0


def _csv_for_type(mat_type: str) -> Path:
    t = mat_type.lower()
    if t == "plate":  return DATA_DIR / "plates_dataset.csv"
    if t == "gasket": return DATA_DIR / "gaskets_dataset.csv"
    return DATA_DIR / "plates_gaskets_combined.csv"


def _parse_factory_code(raw: str) -> str:
    return raw.strip().split()[0].upper()


def _label(code: str) -> str:
    name = FACTORY_NAMES.get(code, "")
    return f"{code} ({name})" if name else code


def _cap_risk(util: float) -> str:
    if util >= UTIL_CRITICAL: return "CRITICAL"
    if util >= UTIL_WARN:     return "MODERATE"
    return "LOW"


def _stock_delivery(transport_cd: int):
    return TODAY + timedelta(days=transport_cd)


def _prod_delivery(shortage: float, ops_per_week: float,
                   lt_weeks: float, transport_cd: int):
    if ops_per_week >= MIN_OPS_PER_WEEK:
        prod_weeks = math.ceil(shortage / ops_per_week)
    else:
        prod_weeks = int(lt_weeks)
    return TODAY + timedelta(days=int(prod_weeks * 7) + transport_cd)


def _solution_a(records: list, qty: int, req_date) -> dict:
    candidates = []
    for r in records:
        stock_del = _stock_delivery(r["transport_cd"])
        if stock_del <= req_date and r["inv_usable"] > 0:
            candidates.append({**r, "stock_del": stock_del})

    candidates.sort(key=lambda x: -x["inv_usable"])

    legs, remaining = [], qty
    for c in candidates:
        if remaining <= 0:
            break
        give = min(c["inv_usable"], remaining)
        legs.append({
            "plant":      c["plant"],
            "name":       _label(c["plant"]),
            "qty":        int(give),
            "from_stock": int(give),
            "from_prod":  0,
            "delivery":   c["stock_del"],
            "cost_unit":  c["cost_per_unit"],
            "line_cost":  round(c["cost_per_unit"] * give, 2),
            "util":       c["util"],
            "cap_risk":   _cap_risk(c["util"]),
        })
        remaining -= give

    covered    = sum(l["qty"] for l in legs)
    shortfall  = max(0, qty - covered)
    last_date  = max((l["delivery"] for l in legs), default=None)
    total_cost = round(sum(l["line_cost"] for l in legs), 2)
    n_fac      = len(legs)

    if shortfall == 0:
        status = "FULL"
        note   = (f"All {qty:,} pcs shipped from stock on time using "
                  f"{n_fac} {'factory' if n_fac == 1 else 'factories'}.")
    elif covered > 0:
        status = "PARTIAL"
        note   = (f"{covered:,}/{qty:,} pcs available from stock by {req_date}. "
                  f"{shortfall:,} pcs need production — see Solution B.")
    else:
        status = "NONE"
        note   = "No factory has on-time stock. See Solution B for earliest option."

    return {
        "status": status, "note": note, "legs": legs,
        "covered": covered, "shortfall": shortfall,
        "last_date": last_date, "total_cost": total_cost, "n_factories": n_fac,
    }


def _solution_b(records: list, qty: int) -> dict:
    options = []
    for r in records:
        inv      = r["inv_usable"]
        ops_pw   = r["ops_per_week"]
        transport = r["transport_cd"]
        lt_weeks  = r["lt_weeks"]

        if inv >= qty:
            delivery   = _stock_delivery(transport)
            prod_weeks = 0
            from_stock = qty
            from_prod  = 0
        else:
            shortage   = qty - inv
            delivery   = _prod_delivery(shortage, ops_pw, lt_weeks, transport)
            prod_weeks = math.ceil(shortage / ops_pw) if ops_pw >= MIN_OPS_PER_WEEK else int(lt_weeks)
            from_stock = int(inv)
            from_prod  = qty - from_stock

        options.append({
            "plant":      r["plant"],
            "name":       _label(r["plant"]),
            "delivery":   delivery,
            "prod_weeks": prod_weeks,
            "from_stock": from_stock,
            "from_prod":  from_prod,
            "util":       r["util"],
            "cap_risk":   _cap_risk(r["util"]),
            "cost_unit":  r["cost_per_unit"],
            "total_cost": round(r["cost_per_unit"] * qty, 2),
            "transport":  transport,
            "ops_pw":     ops_pw,
        })

    options.sort(key=lambda x: x["delivery"])
    return {"best": options[0] if options else None, "all": options}


def check_material(df: pd.DataFrame, mat_id: str, qty: int,
                   preferred_factory: str, req_date) -> dict:
    key_cols = [
        "Plant_2_6", "Sap code_2_6", "Material description_2_6",
        "inv_usable_qty_3_1", "Stock Qty_3_1",
        "cap_utilization_pct", "Production LT Weeks_2_3",
        "Planned Delivery Time (MARC) (CD)_2_3",
        "Standard Cost in EUR_2_3", "ops_avg_planned_pcs_per_week",
    ]
    existing = [c for c in key_cols if c in df.columns]
    rows = (df[df["Sap code_2_6"] == mat_id][existing]
            .drop_duplicates(subset=["Plant_2_6"]).copy())

    if rows.empty:
        return {
            "material": mat_id, "description": "", "qty": qty,
            "preferred": preferred_factory, "records": [],
            "sol_a": None, "sol_b": None,
            "error": f"Material {mat_id} not found in dataset.",
        }

    description = str(rows.iloc[0].get("Material description_2_6", ""))
    records = []
    for _, r in rows.iterrows():
        plant    = str(r.get("Plant_2_6", "?"))
        inv      = float(r.get("inv_usable_qty_3_1") or 0)
        util_raw = r.get("cap_utilization_pct")
        util     = float(util_raw) if util_raw is not None and str(util_raw) != "nan" else 0.0
        lt_weeks = float(r.get("Production LT Weeks_2_3") or 4)
        transport = int(r.get("Planned Delivery Time (MARC) (CD)_2_3") or TRANSPORT_DAYS)
        cost     = float(r.get("Standard Cost in EUR_2_3") or 0)
        ops_pw   = float(r.get("ops_avg_planned_pcs_per_week") or 0)
        records.append({
            "plant": plant, "is_preferred": plant == preferred_factory,
            "inv_usable": inv, "util": util, "lt_weeks": lt_weeks,
            "transport_cd": transport, "cost_per_unit": cost, "ops_per_week": ops_pw,
        })

    sol_a = _solution_a(records, qty, req_date)
    sol_b = None if sol_a["status"] == "FULL" else _solution_b(records, qty)
    pref_exists = any(r["is_preferred"] for r in records)

    return {
        "material": mat_id, "description": description, "qty": qty,
        "preferred": preferred_factory, "pref_exists": pref_exists,
        "records": records, "sol_a": sol_a, "sol_b": sol_b, "error": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SHARED INVENTORY CACHE  (deducted in rank order)
# ══════════════════════════════════════════════════════════════════════════════

_CSV_CACHE: dict = {}


def _get_df(mat_type: str) -> pd.DataFrame:
    key = mat_type.lower()
    if key not in _CSV_CACHE:
        path = _csv_for_type(mat_type)
        if not path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")
        _CSV_CACHE[key] = pd.read_csv(path)
    return _CSV_CACHE[key]


def reset_cache():
    """Call before each pipeline run to restore inventory from disk."""
    _CSV_CACHE.clear()


def _deduct_inventory(mat_type: str, mat_id: str, plant: str, qty_used: int):
    df = _get_df(mat_type)
    mask = (df["Sap code_2_6"] == mat_id) & (df["Plant_2_6"] == plant)
    df.loc[mask, "inv_usable_qty_3_1"] = (
        df.loc[mask, "inv_usable_qty_3_1"] - qty_used
    ).clip(lower=0)


# ══════════════════════════════════════════════════════════════════════════════
# RANKING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

TIER_SCORES = {"Strategic": 1.0, "Large": 0.75, "Medium": 0.5, "Small": 0.25}
PROB_W = 0.50
TIER_W = 0.30
LOY_W  = 0.20


def load_history() -> dict:
    """Return {owner_name: project_count} from project list CSV for loyalty scoring."""
    csv_path = EXT_DIR / "1_3_Export_Project_list.csv"
    if not csv_path.exists():
        return {}
    df = pd.read_csv(csv_path)
    col = next((c for c in ["Owner", "owner", "Customer"] if c in df.columns), None)
    if col is None:
        return {}
    return df[col].dropna().value_counts().to_dict()


def _enrich(order: dict) -> dict:
    cust = order.get("customer", {})
    mats = order.get("materials_requested", [])
    order["owner"]        = cust.get("name", order.get("owner", "Unknown"))
    order["probability"]  = int(cust.get("probability", order.get("probability", 50)))
    order["revenue_tier"] = cust.get("revenue_tier", order.get("revenue_tier", "Medium"))
    if "expected_pcs" not in order:
        order["expected_pcs"] = sum(int(m.get("quantity_pcs", 0)) for m in mats)
    if "expected_eur" not in order:
        order["expected_eur"] = 0
    return order


def rank_orders(orders: list, hist: dict) -> list:
    for o in orders:
        prob    = o.get("probability", 50) / 100
        tier    = TIER_SCORES.get(o.get("revenue_tier", "Medium"), 0.5)
        raw_loy = hist.get(o.get("owner", ""), 0)
        loyalty = min(raw_loy / 20, 1.0)
        o["hist_orders"]     = raw_loy
        o["composite_score"] = round(prob * PROB_W + tier * TIER_W + loyalty * LOY_W, 4)

    ranked = sorted(orders, key=lambda x: -x["composite_score"])
    for i, o in enumerate(ranked, 1):
        o["rank"] = i
    return ranked


# ══════════════════════════════════════════════════════════════════════════════
# HARDCODED BASE ORDERS
# ══════════════════════════════════════════════════════════════════════════════

HARDCODED_ORDERS = [
    {
        "order_id": "ORD-001",
        "customer": {
            "name": "Danfoss",
            "segment": "Marine",
            "region": "EMEA-East",
            "probability": 75,
            "revenue_tier": "Medium",
        },
        "order": {
            "requested_delivery_date": "2026-07-18",
            "preferred_factory": "NW05",
            "priority": "Standard",
        },
        "materials_requested": [
            {"material_number": "MAT-200635", "description": "M6-FKM GASKET 36",
             "type": "Gasket", "quantity_pcs": 10000},
        ],
    },
    {
        "order_id": "ORD-002",
        "customer": {
            "name": "Danfoss",
            "segment": "District Heating",
            "region": "EMEA-East",
            "probability": 25,
            "revenue_tier": "Large",
        },
        "order": {
            "requested_delivery_date": "2026-07-18",
            "preferred_factory": "NW03",
            "priority": "Urgent",
        },
        "materials_requested": [
            {"material_number": "MAT-200696", "description": "M10-BFG GASKET 17",
             "type": "Gasket", "quantity_pcs": 500},
            {"material_number": "MAT-200584", "description": "M10-BFG GASKET 25",
             "type": "Gasket", "quantity_pcs": 500},
            {"material_number": "MAT-200128", "description": "M10-BFG GASKET 09",
             "type": "Gasket", "quantity_pcs": 5000},
            {"material_number": "MAT-100585", "description": "MX200/PL.36 304TL 0.4mm",
             "type": "Plate", "quantity_pcs": 5000},
        ],
    },
    {
        "order_id": "ORD-003",
        "customer": {
            "name": "AGRAMKOW",
            "segment": "District Cooling",
            "region": "EMEA-East",
            "probability": 50,
            "revenue_tier": "Medium",
        },
        "order": {
            "requested_delivery_date": "2026-07-18",
            "preferred_factory": "NW14",
            "priority": "Urgent",
        },
        "materials_requested": [
            {"material_number": "MAT-200128", "description": "M10-BFG GASKET 09",
             "type": "Gasket", "quantity_pcs": 5000},
            {"material_number": "MAT-100585", "description": "MX200/PL.36 304TL 0.4mm",
             "type": "Plate", "quantity_pcs": 5000},
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# LOAD NEW ORDER FROM temp_selection.json
# ══════════════════════════════════════════════════════════════════════════════

def load_new_order() -> dict | None:
    """Load the latest order from temp_selection.json, assign next sequential ID."""
    if not SEL_FILE.exists():
        return None
    with open(SEL_FILE, encoding="utf-8") as f:
        order = json.load(f)

    # assign next ID after the last hardcoded order
    existing_ids = [o["order_id"] for o in HARDCODED_ORDERS]
    max_num = 0
    for oid in existing_ids:
        try:
            max_num = max(max_num, int(oid.split("-")[1]))
        except (IndexError, ValueError):
            pass
    order["order_id"] = f"ORD-{max_num + 1:03d}"
    return order


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(orders_raw: list) -> list:
    """
    Rank orders, then run feasibility in rank order (inventory depleted as we go).
    Returns ranked list — each order has a 'results' key with per-material checks.
    """
    reset_cache()
    orders = [_enrich(o) for o in orders_raw]
    hist   = load_history()
    ranked = rank_orders(orders, hist)

    for r in ranked:
        preferred = _parse_factory_code(r["order"].get("preferred_factory", "NW01"))
        req_date  = datetime.strptime(
            r["order"].get("requested_delivery_date", str(TODAY)), "%Y-%m-%d"
        ).date()

        results = []
        for item in r.get("materials_requested", []):
            mat_id = item["material_number"].strip()
            qty    = int(item["quantity_pcs"])
            m_type = item.get("type", "Plate")
            df     = _get_df(m_type)
            res    = check_material(df, mat_id, qty, preferred, req_date)
            results.append(res)

            # higher-ranked orders consume stock first
            if res.get("sol_a") and res["sol_a"]["legs"]:
                for leg in res["sol_a"]["legs"]:
                    _deduct_inventory(m_type, mat_id, leg["plant"], leg["qty"])

        r["results"]    = results
        r["req_date"]   = req_date
        r["preferred"]  = preferred

    return ranked


# ══════════════════════════════════════════════════════════════════════════════
# CLI PRINTER
# ══════════════════════════════════════════════════════════════════════════════

W   = 78
SEP = "=" * W
DIV = "-" * W


def _print_ranking(ranked: list):
    print(f"\n{SEP}")
    print(f"  STEP 1  —  ORDER PRIORITY RANKING")
    print(SEP)
    print(f"\n  {'Rnk':<4} {'Order ID':<12} {'Customer':<18} {'Prob':>6}  "
          f"{'Tier':<12} {'Loyalty':>8}  {'Score':>7}  {'PCS':>8}")
    print(f"  {DIV}")
    for r in ranked:
        tag = "  ◀ FIRST" if r["rank"] == 1 else ""
        print(f"  #{r['rank']:<3} {r.get('order_id','?'):<12} "
              f"{r.get('owner','?')[:17]:<18} {r['probability']:>5}%  "
              f"{r['revenue_tier']:<12} {r['hist_orders']:>8}  "
              f"{r['composite_score']:>7.4f}  {r['expected_pcs']:>8,}{tag}")
    print(f"\n  Inventory rule: Rank 1 consumes stock first, then Rank 2, ...")
    print(SEP)


def _print_feasibility(ranked: list):
    print(f"\n{SEP}")
    print(f"  STEP 2  —  FEASIBILITY CHECKS  (in rank order)")
    print(SEP)

    for r in ranked:
        oid      = r.get("order_id", f"ORD-{r['rank']:03d}")
        cust     = r.get("customer", {})
        req_date = r["req_date"]
        days_left = (req_date - TODAY).days

        print(f"\n{SEP}")
        print(f"  RANK #{r['rank']}  |  {oid}  —  {cust.get('name','?')}")
        print(f"  Score: {r['composite_score']:.4f}  |  "
              f"Prob: {r['probability']}%  |  Tier: {r['revenue_tier']}  |  "
              f"Loyalty: {r['hist_orders']} projects")
        print(f"  Deadline: {req_date}  ({days_left}d remaining)  |  "
              f"Factory: {r['preferred']}")
        print(DIV)

        for res in r.get("results", []):
            if res.get("error"):
                print(f"\n  ✗ {res['material']}  —  {res['error']}")
                continue

            mat  = res["material"]
            desc = res["description"]
            qty  = res["qty"]
            sa   = res["sol_a"]
            sb   = res["sol_b"]

            a_icon = {"FULL": "✅", "PARTIAL": "⚠️", "NONE": "❌"}.get(sa["status"] if sa else "NONE", "❌")
            print(f"\n  {a_icon}  {desc}  ({mat})  —  {qty:,} pcs")

            if sa:
                print(f"     Solution A ({sa['status']}): {sa['note']}")
                if sa["legs"]:
                    for leg in sa["legs"]:
                        print(f"       → {leg['name']:<30} {leg['qty']:>8,} pcs  "
                              f"ships {leg['delivery']}  €{leg['line_cost']:>10,.0f}")
                    print(f"       {'TOTAL':<30} {sa['covered']:>8,} pcs  "
                          f"by     {sa['last_date']}  €{sa['total_cost']:>10,.0f}")

            if sb and sb["best"]:
                best = sb["best"]
                delta = (req_date - best["delivery"]).days
                ot = f"+{delta}d buffer" if delta >= 0 else f"{abs(delta)}d LATE"
                print(f"     Solution B (single factory): {best['name']}")
                print(f"       Delivery: {best['delivery']}  [{ot}]  "
                      f"Cost: €{best['total_cost']:,.0f}")

    print(f"\n{SEP}\n")


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Northwind Order Pipeline")
    parser.add_argument("--orders", help="Path to orders JSON file")
    args = parser.parse_args()

    if args.orders:
        with open(args.orders) as f:
            raw = json.load(f)
        orders = raw if isinstance(raw, list) else [raw]
    else:
        orders = list(HARDCODED_ORDERS)
        new_order = load_new_order()
        if new_order:
            print(f"  Loaded new order from temp_selection.json → {new_order['order_id']}\n")
            orders.append(new_order)
        else:
            print("  No temp_selection.json found — running 3 hardcoded orders.\n")

    ranked = run_pipeline(orders)
    _print_ranking(ranked)
    _print_feasibility(ranked)


if __name__ == "__main__":
    main()

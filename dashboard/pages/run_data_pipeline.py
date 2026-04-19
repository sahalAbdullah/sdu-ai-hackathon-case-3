"""
Order Feasibility Checker  --  Two-solution output
  Solution A: Meet the deadline  -- fewest factories, all pcs on time
  Solution B: One factory, any date -- only shown when Solution A is not FULL

Usage:
  python run_data_pipeline.py                        # auto-loads temp_selection.json
  python run_data_pipeline.py --order order.json
  python run_data_pipeline.py --material MAT-100636 --qty 15000 --factory NW06 --date 2026-07-17 --type Plate
"""

import json
import math
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

# ── constants ────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).parent.parent.parent
DATA_DIR       = ROOT / "data" / "Dataset-Parsed"
SEL_FILE       = ROOT / "temp_selection.json"
TODAY          = datetime.today().date()
UTIL_WARN      = 0.80
UTIL_CRITICAL  = 0.95
TRANSPORT_DAYS = 45


TEST_ORDER = {
    "customer": {"name": "Danfoss", "segment": "Oil & Gas", "region": "LATAM"},
    "order": {
        "requested_delivery_date": "2026-07-17",
        "preferred_factory": "NW06",
        "priority": "Urgent",
        "notes": "None",
    },
    "materials_requested": [
        {
            "material_number": "MAT-100585",
            "description": "MX200/PL.36 304TL 0.4mm",
            "type": "Plate",
            "quantity_pcs": 500,
        }
    ],
}

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


# ── helpers ───────────────────────────────────────────────────────────────────
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


MIN_OPS_PER_WEEK = 1.0

def _prod_delivery(shortage: float, ops_per_week: float,
                   lt_weeks: float, transport_cd: int):
    if ops_per_week >= MIN_OPS_PER_WEEK:
        prod_weeks = math.ceil(shortage / ops_per_week)
    else:
        prod_weeks = int(lt_weeks)
    return TODAY + timedelta(days=int(prod_weeks * 7) + transport_cd)


# ─────────────────────────────────────────────────────────────────────────────
# SOLUTION A  --  On-time delivery from STOCK, fewest factories
# ─────────────────────────────────────────────────────────────────────────────
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
            "stock_del":  c["stock_del"],
            "prod_del":   None,
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
                  f"{shortfall:,} pcs have no on-time stock -- see Solution B "
                  f"for production-based fulfillment from a single factory.")
    else:
        status = "NONE"
        note   = ("No factory holds stock that arrives by the deadline. "
                  "See Solution B for the earliest single-factory option.")

    return {
        "status":      status,
        "note":        note,
        "legs":        legs,
        "covered":     covered,
        "shortfall":   shortfall,
        "last_date":   last_date,
        "total_cost":  total_cost,
        "n_factories": n_fac,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SOLUTION B  --  One factory, full quantity, flexible date
# Only computed when Solution A is not FULL.
# ─────────────────────────────────────────────────────────────────────────────
def _solution_b(records: list, qty: int) -> dict:
    options = []
    for r in records:
        inv       = r["inv_usable"]
        ops_pw    = r["ops_per_week"]
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
            if ops_pw >= MIN_OPS_PER_WEEK:
                prod_weeks = math.ceil(shortage / ops_pw)
            else:
                prod_weeks = int(lt_weeks)
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


# ── core: load per-factory data ───────────────────────────────────────────────
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
            .drop_duplicates(subset=["Plant_2_6"])
            .copy())

    if rows.empty:
        return {
            "material": mat_id, "description": "",
            "qty": qty, "preferred": preferred_factory,
            "records": [], "sol_a": None, "sol_b": None,
            "error": f"Material {mat_id} not found in dataset.",
        }

    description = str(rows.iloc[0].get("Material description_2_6", ""))

    records = []
    for _, r in rows.iterrows():
        plant     = str(r.get("Plant_2_6", "?"))
        inv       = float(r.get("inv_usable_qty_3_1") or 0)
        stock     = float(r.get("Stock Qty_3_1") or 0)
        util_raw  = r.get("cap_utilization_pct")
        util      = float(util_raw) if util_raw is not None and str(util_raw) != "nan" else 0.0
        lt_weeks  = float(r.get("Production LT Weeks_2_3") or 4)
        transport = int(r.get("Planned Delivery Time (MARC) (CD)_2_3") or TRANSPORT_DAYS)
        cost      = float(r.get("Standard Cost in EUR_2_3") or 0)
        ops_pw    = float(r.get("ops_avg_planned_pcs_per_week") or 0)

        records.append({
            "plant":         plant,
            "is_preferred":  plant == preferred_factory,
            "inv_usable":    inv,
            "stock_qty":     stock,
            "util":          util,
            "lt_weeks":      lt_weeks,
            "transport_cd":  transport,
            "cost_per_unit": cost,
            "ops_per_week":  ops_pw,
        })

    sol_a = _solution_a(records, qty, req_date)

    # Solution B is skipped when Solution A fully covers the order on time
    sol_b = None if sol_a["status"] == "FULL" else _solution_b(records, qty)

    pref_exists = any(r["is_preferred"] for r in records)

    return {
        "material":    mat_id,
        "description": description,
        "qty":         qty,
        "preferred":   preferred_factory,
        "pref_exists": pref_exists,
        "records":     records,
        "sol_a":       sol_a,
        "sol_b":       sol_b,
        "error":       None,
    }


# ── printer ───────────────────────────────────────────────────────────────────
W   = 76
SEP = "=" * W
DIV = "-" * W


def _ot_tag(delivery, req_date) -> str:
    delta = (req_date - delivery).days
    if delta >= 0:
        return f"ON TIME  (+{delta}d buffer)"
    return f"LATE  ({-delta}d past deadline)"


def print_report(order: dict, results: list):
    cust  = order.get("customer", {})
    ord_  = order.get("order", {})
    req_date  = datetime.strptime(ord_["requested_delivery_date"], "%Y-%m-%d").date()
    days_left = (req_date - TODAY).days

    print(f"\n{SEP}")
    print(f"  FEASIBILITY REPORT  --  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(SEP)
    print(f"  CUSTOMER   : {cust.get('name','?')}")
    print(f"  Segment    : {cust.get('segment','?')}  |  Region: {cust.get('region','?')}")
    print(f"  Priority   : {ord_.get('priority','Normal')}")
    print(f"  Deadline   : {req_date}  (today: {TODAY}  --  {days_left} days remaining)")
    print(f"  Pref. Fac. : {_label(ord_.get('preferred_factory','?'))}")

    for res in results:
        if res.get("error"):
            print(f"\n  ERROR: {res['error']}")
            continue

        mat  = res["material"]
        desc = res["description"]
        qty  = res["qty"]
        pref = res["preferred"]

        print(f"\n{DIV}")
        print(f"  MATERIAL   : {desc}")
        print(f"  Mat. No.   : {mat}  |  Qty: {qty:,} pcs")
        if not res["pref_exists"]:
            avail = ", ".join(r["plant"] for r in res["records"])
            print(f"  NOTE       : {pref} does NOT produce this material.")
            print(f"               Producing factories: {avail}")
        print(DIV)

        sa = res["sol_a"]
        a_icons = {"FULL": "[GREEN] FULL", "PARTIAL": "[AMBER] PARTIAL",
                   "NONE": "[RED]   NOT POSSIBLE"}
        print(f"\n  SOLUTION A  --  MEET THE DEADLINE  ({req_date})")
        print(f"  Status     : {a_icons.get(sa['status'], sa['status'])}")
        print(f"  {sa['note']}")

        if sa["legs"]:
            print(f"\n  {'#':<3} {'Factory':<28} {'Qty (pcs)':>10}  "
                  f"{'Ships by':<13}  {'Cap%':<14}  {'Line EUR':>12}")
            print(f"  {'-'*78}")
            for i, leg in enumerate(sa["legs"], 1):
                cap_str = f"{leg['util']*100:.0f}% {leg['cap_risk']}"
                print(f"  {i:<3} {leg['name']:<28} {leg['qty']:>10,}  "
                      f"{str(leg['delivery']):<13}  {cap_str:<14}  "
                      f"{leg['line_cost']:>12,.2f}")
            print(f"  {'-'*78}")
            n_fac    = sa["n_factories"]
            fac_word = "factory" if n_fac == 1 else "factories"
            print(f"  {'TOTAL  (' + str(n_fac) + ' ' + fac_word + ')':<32} {sa['covered']:>10,}  "
                  f"{str(sa['last_date']):<13}  {'':14}  {sa['total_cost']:>12,.2f}")
            if sa["shortfall"] > 0:
                print(f"\n  Stock gap  : {sa['shortfall']:,} pcs -- see Solution B")

        sb = res["sol_b"]
        if sb is not None:
            best = sb["best"]
            print(f"\n  SOLUTION B  --  ONE FACTORY, FULL QUANTITY  (flexible date)")
            print(f"  Strategy   : Single source for all {qty:,} pcs; produce the gap if needed")

            if best:
                ot = _ot_tag(best["delivery"], req_date)
                print(f"\n  Best factory : {best['name']}")
                print(f"  Delivery     : {best['delivery']}  [{ot}]")
                print(f"  From stock   : {best['from_stock']:,} pcs")
                print(f"  From prod.   : {best['from_prod']:,} pcs")
                print(f"  Cost/unit    : EUR {best['cost_unit']:.2f}")
                print(f"  TOTAL COST   : EUR {best['total_cost']:,.2f}")

                print(f"\n  All options (earliest delivery first):")
                print(f"  {'#':<3} {'Factory':<28} {'Delivery':<13} {'Prod wks':>9}"
                      f"  {'Cap%':<6}  {'EUR total':>12}")
                print(f"  {'-'*72}")
                for i, o in enumerate(sb["all"], 1):
                    tag = "  <-- BEST" if o == best else ""
                    ot2 = _ot_tag(o["delivery"], req_date)
                    ot_short = "ON-TIME" if "ON TIME" in ot2 else "LATE   "
                    print(f"  {i:<3} {o['name']:<28} {str(o['delivery']):<13} "
                          f"{o['prod_weeks']:>9}  {o['util']*100:<6.0f}  "
                          f"{o['total_cost']:>12,.2f}  [{ot_short}]{tag}")

        print(f"\n  SUMMARY")
        print(f"  {DIV[:68]}")
        if sa["status"] == "FULL":
            n  = sa["n_factories"]
            fw = "factory" if n == 1 else "factories"
            print(f"  A) ON-TIME : {sa['covered']:,} pcs  |  "
                  f"{n} {fw}  |  by {sa['last_date']}  |  EUR {sa['total_cost']:,.2f}")
            print(f"  B) SKIPPED : Solution A covers the full order on time.")
        elif sa["status"] == "PARTIAL":
            print(f"  A) ON-TIME : {sa['covered']:,}/{qty:,} pcs  |  "
                  f"{sa['n_factories']} factories  |  by {sa['last_date']}  |  "
                  f"EUR {sa['total_cost']:,.2f}  (partial)")
            if sb and sb["best"]:
                best = sb["best"]
                ot3  = _ot_tag(best["delivery"], req_date)
                print(f"  B) ONE FAC : {qty:,} pcs  |  {best['name']}  |  "
                      f"by {best['delivery']}  |  EUR {best['total_cost']:,.2f}  [{ot3}]")
        else:
            print(f"  A) ON-TIME : NOT POSSIBLE before {req_date}")
            if sb and sb["best"]:
                best = sb["best"]
                ot3  = _ot_tag(best["delivery"], req_date)
                print(f"  B) ONE FAC : {qty:,} pcs  |  {best['name']}  |  "
                      f"by {best['delivery']}  |  EUR {best['total_cost']:,.2f}  [{ot3}]")

    print(f"\n{SEP}\n")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Order Feasibility Checker")
    parser.add_argument("--order",    help="Path to order JSON file")
    parser.add_argument("--material", help="Material number e.g. MAT-100585")
    parser.add_argument("--qty",      type=int)
    parser.add_argument("--factory",  help="Preferred factory code e.g. NW06")
    parser.add_argument("--date",     help="Requested delivery date YYYY-MM-DD")
    parser.add_argument("--type",     default="Plate",
                        choices=["Plate", "Gasket", "Both"])
    args = parser.parse_args()

    if args.order:
        with open(args.order) as f:
            raw = json.load(f)
        orders = raw if isinstance(raw, list) else [raw]
    elif args.material:
        orders = [{
            "customer": {"name": "CLI", "segment": "-", "region": "-"},
            "order": {
                "requested_delivery_date": args.date or str(TODAY + timedelta(days=90)),
                "preferred_factory": args.factory or "NW01",
                "priority": "Normal",
            },
            "materials_requested": [
                {"material_number": args.material, "description": "",
                 "type": args.type, "quantity_pcs": args.qty or 100}
            ],
        }]
    else:
        if SEL_FILE.exists():
            print(f"Loading order from {SEL_FILE}\n")
            with open(SEL_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            orders = raw if isinstance(raw, list) else [raw]
        else:
            print("No order supplied and temp_selection.json not found -- running built-in test order.\n")
            orders = [TEST_ORDER]

    for order in orders:
        preferred = _parse_factory_code(order["order"].get("preferred_factory", "NW01"))
        order["order"]["preferred_factory"] = preferred
        req_date = datetime.strptime(
            order["order"]["requested_delivery_date"], "%Y-%m-%d").date()

        results = []
        for item in order["materials_requested"]:
            mat_id   = item["material_number"].strip()
            qty      = int(item["quantity_pcs"])
            m_type   = item.get("type", "Plate")
            csv_path = _csv_for_type(m_type)
            if not csv_path.exists():
                print(f"ERROR: CSV not found: {csv_path}")
                sys.exit(1)
            df = pd.read_csv(csv_path)
            results.append(check_material(df, mat_id, qty, preferred, req_date))

        print_report(order, results)


if __name__ == "__main__":
    main()

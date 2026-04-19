"""
Multi-agent order feasibility analysis for Northwind.

Phase 1 (parallel): Calendar Agent + Tool Routing Agent + Lead Time Agent
Phase 2 (parallel): Capacity Agent + Production Plan Agent  (need phase-1 outputs)
Phase 3:            Master Agent synthesises all five reports into a final brief

Run:  python scripts/run_analysis.py
"""

import json, subprocess, sys, concurrent.futures, tempfile, os
from pathlib import Path
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT   = Path(__file__).parent.parent
DATA   = ROOT / "data" / "extracted"
AGENTS = ROOT / "agents"
SEL    = ROOT / "temp_selection.json"
CLAUDE = r"C:\Users\sahal\.local\bin\claude.exe"


# ── helpers ───────────────────────────────────────────────────────────────────

def read_agent_instructions(name: str) -> str:
    return (AGENTS / name).read_text(encoding="utf-8")


def _write_temp(text: str) -> str:
    """Write text to a temp file, return the path. Caller must delete it."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as f:
        f.write(text)
    return path


def call_claude(prompt: str, label: str) -> str:
    """Call Claude CLI, return full stdout. Uses stdin to avoid Windows 32K cmd limit."""
    print(f"  → [{label}] running...", flush=True)
    tmp = _write_temp(prompt)
    try:
        with open(tmp, "r", encoding="utf-8", errors="replace") as stdin_f:
            proc = subprocess.run(
                [CLAUDE, "--print", "--dangerously-skip-permissions"],
                stdin=stdin_f,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        out = proc.stdout.strip()
    finally:
        os.unlink(tmp)
    print(f"  ✓ [{label}] done  ({len(out):,} chars)", flush=True)
    return out


def call_claude_stream(prompt: str) -> None:
    """Call Claude CLI and stream output to stdout (for the final brief)."""
    tmp = _write_temp(prompt)
    try:
        with open(tmp, "r", encoding="utf-8", errors="replace") as stdin_f:
            proc = subprocess.Popen(
                [CLAUDE, "--print", "--dangerously-skip-permissions"],
                stdin=stdin_f,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            for line in proc.stdout:
                print(line, end="", flush=True)
            proc.wait()
    finally:
        os.unlink(tmp)


# ── load customer request ─────────────────────────────────────────────────────

with open(SEL, encoding="utf-8") as f:
    req = json.load(f)

mats        = req["materials_requested"]
mat_nums    = [m["material_number"] for m in mats]
plant_raw   = req["order"]["preferred_factory"]
# handles both "–" (en/em dash) and plain "-"
plant_code  = plant_raw.replace("\u2013", "-").replace("\u2014", "-").split("-")[0].strip()
delivery    = req["order"]["requested_delivery_date"]

customer_ctx = f"""CUSTOMER REQUEST:
{json.dumps(req, indent=2)}

Materials  : {mat_nums}
Plant      : {plant_code}
Delivery   : {delivery}
Today      : 2026-04-18
"""

print("=" * 60)
print("NORTHWIND MULTI-AGENT ORDER ANALYSIS")
print(f"  Customer  : {req['customer']['name']}")
print(f"  Materials : {mat_nums}")
print(f"  Plant     : {plant_code}")
print(f"  Delivery  : {delivery}")
print("=" * 60)


# ── pre-filter data for each agent ───────────────────────────────────────────

print("\nPre-loading and filtering data files...", flush=True)

# Tool Routing Agent  →  2_6
df6          = pd.read_csv(DATA / "2_6_Tool_material_nr_master.csv")
routing_data = df6[df6["Sap code"].isin(mat_nums)].to_csv(index=False)

# Lead Time Agent  →  2_3
df3          = pd.read_csv(DATA / "2_3_SAP_MasterData.csv")
leadtime_data = df3[df3["Sap code"].isin(mat_nums)].to_csv(index=False)

# Production Plan Agent  →  2_2 keys (non-time-series columns)
df_ops       = pd.read_csv(DATA / "2_2_OPS_plan_per_material_keys.csv")
ops_data     = df_ops[
    df_ops["P80 - Plant Material: Pure Material"].isin(mat_nums)
].to_csv(index=False)

# Calendar Agent  →  2_4 summary JSON (small, pass in full)
with open(DATA / "2_4_Model_Calendar_summary.json", encoding="utf-8") as f:
    calendar_data = f.read()

# Capacity Agent  →  2_1 keys (filter by plant) + 2_5 (filter by plant)
df_wc        = pd.read_csv(DATA / "2_1_Work_Center_Capacity_Weekly_keys.csv")
df_sched     = pd.read_csv(DATA / "2_5_WC_Schedule_limits.csv")
wc_data      = df_wc[df_wc["Work center code"].str.contains(plant_code, na=False)].to_csv(index=False)
sched_data   = df_sched[df_sched["Plant"] == plant_code].to_csv(index=False)

print("  Data filtered. Launching agents.\n", flush=True)


# ── agent instruction texts ───────────────────────────────────────────────────

instr_routing  = read_agent_instructions("tool_routing_agent.md")
instr_leadtime = read_agent_instructions("lead_time_agent.md")
instr_calendar = read_agent_instructions("calendar_agent.md")
instr_ops      = read_agent_instructions("production_plan_agent.md")
instr_capacity = read_agent_instructions("capacity_agent.md")


# ── Phase 1: Calendar + Routing + Lead Time (all independent) ─────────────────

print("[PHASE 1]  Calendar  |  Tool Routing  |  Lead Time  — running in parallel")

def run_calendar():
    prompt = f"""{instr_calendar}

{customer_ctx}

CALENDAR DATA (2_4_Model_Calendar_summary.json):
{calendar_data}

Produce your full Calendar Report now. Use the exact output format from your instructions."""
    return call_claude(prompt, "Calendar")


def run_routing():
    prompt = f"""{instr_routing}

{customer_ctx}

TOOL & ROUTING DATA (2_6 — rows for the requested materials only):
{routing_data}

Produce your full routing report now. Use the exact output format from your instructions."""
    return call_claude(prompt, "Routing")


def run_leadtime():
    prompt = f"""{instr_leadtime}

{customer_ctx}

SAP LEAD TIME & COST DATA (2_3 — rows for the requested materials only):
{leadtime_data}

Produce your full lead time and cost report now. Use the exact output format from your instructions."""
    return call_claude(prompt, "LeadTime")


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    fut_cal  = pool.submit(run_calendar)
    fut_rout = pool.submit(run_routing)
    fut_lt   = pool.submit(run_leadtime)
    calendar_out = fut_cal.result()
    routing_out  = fut_rout.result()
    leadtime_out = fut_lt.result()

print()


# ── Phase 2: Capacity + Production Plan (use phase-1 outputs) ─────────────────

print("[PHASE 2]  Capacity  |  Production Plan  — running in parallel")

def run_capacity():
    prompt = f"""{instr_capacity}

{customer_ctx}

ROUTING AGENT REPORT (use this to identify which work centers to check):
{routing_out}

CALENDAR AGENT REPORT (use this to identify which weeks to check):
{calendar_out}

WORK CENTER CAPACITY DATA (2_1 — rows for plant {plant_code} only):
{wc_data}

SHIFT SCHEDULE LIMITS (2_5 — rows for plant {plant_code} only):
{sched_data}

Produce your full capacity check report now. Use the exact output format from your instructions."""
    return call_claude(prompt, "Capacity")


def run_ops():
    prompt = f"""{instr_ops}

{customer_ctx}

CALENDAR AGENT REPORT (use this to identify the target week range):
{calendar_out}

PRODUCTION PLAN DATA (2_2 — rows for the requested materials only):
{ops_data}

Produce your full production plan report now. Use the exact output format from your instructions."""
    return call_claude(prompt, "OpsPlan")


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    fut_cap = pool.submit(run_capacity)
    fut_ops = pool.submit(run_ops)
    capacity_out = fut_cap.result()
    ops_out      = fut_ops.result()

print()


# ── Phase 3: Master synthesis ─────────────────────────────────────────────────

print("[PHASE 3]  Master agent synthesising final brief...\n", flush=True)

master_prompt = f"""You are a sales advisor for Northwind manufacturing.
Five specialist agents have analysed a customer order. Use their findings to write a SHORT, plain-English sales brief.
No technical jargon. No tables. No codes. Write like you are briefing a salesperson before they call the customer.

{customer_ctx}

AGENT FINDINGS:
---
CALENDAR: {calendar_out}
---
ROUTING: {routing_out}
---
LEAD TIME & COST: {leadtime_out}
---
CAPACITY: {capacity_out}
---
PRODUCTION PLAN: {ops_out}
---

Write the output in EXACTLY this format — nothing more, nothing less:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SALES BRIEF  —  {req['customer']['name']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ / ⚠️ / ❌  CAN WE DO IT?
  One sentence. Yes / Partially / No, and the single most important reason why.

⏱  HOW LONG?
  One sentence. The realistic delivery date and how many weeks that is from today.

🏭  WHAT IF WE USE ANOTHER FACTORY?
  One or two sentences. Is there a better/faster option at a different plant? If yes, name it and say how much faster.

⚠️  RISKS TO MENTION
  2–3 bullet points. Only the risks that actually matter for this order. Plain words.

💡  OTHER OPTIONS FOR THE CUSTOMER
  1–2 bullet points. Alternatives we could offer (different qty, split delivery, different plant, expedite).

📞  WHAT TO TELL THE CUSTOMER  (one line the salesperson can say verbatim)
  "..."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Rules:
- Use real dates and numbers from the agent reports, but write them in plain English (not codes).
- If data was not found for something, skip that point rather than guessing.
- Keep every section SHORT. The salesperson needs to read this in 30 seconds.
- Today is 2026-04-18.
"""

print("=" * 60)
print(f"SALES BRIEF  —  {req['customer']['name']}")
print("=" * 60 + "\n", flush=True)

call_claude_stream(master_prompt)

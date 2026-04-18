# Predictive Manufacturing — AI Hackathon Starter Pack

Welcome! This is your starting point for the **Predictive Manufacturing** case. Your job over the hackathon is to build a working prototype that predicts production capacity bottlenecks and optimizes raw-material sourcing against a global manufacturing network's sales pipeline.

## What's in this pack

| File | What it is |
|------|------------|
| [CASE_BRIEF.md](CASE_BRIEF.md) | The problem. Read this first. |
| [DATA_DICTIONARY.md](DATA_DICTIONARY.md) | What each sheet and column means, and how the sheets join. Neutral reference — the analysis is yours to design. |
| [HINTS.md](HINTS.md) | Starting-point suggestions from the case owner (do with them as you wish). |
| [data/hackathon_dataset.xlsx](data/hackathon_dataset.xlsx) | The anonymized dataset. 12 sheets, ~26 MB, 15 plants, cleaned row-1 headers, structure identical to the real Danfoss export. |
| [data/Data_Dictionary_overview.xlsx](data/Data_Dictionary_overview.xlsx) | Companion sheet-level overview (NR, name, area, description). |
| [notebooks/starter_notebook.ipynb](notebooks/starter_notebook.ipynb) | A minimal notebook that loads every sheet and prints its shape — so you never have to start from a blank cell. |
| [requirements.txt](requirements.txt) | The Python environment. |
| [scripts/generate_dataset.py](scripts/generate_dataset.py) | The script that produced the dataset, provided for transparency. You do **not** need to run it — the Excel file is already generated. |

## Quick start

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # or: .venv\Scripts\activate on Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch the starter notebook
jupyter notebook notebooks/starter_notebook.ipynb
```

Or in VS Code: open `notebooks/starter_notebook.ipynb`, select a Python kernel in the virtualenv, Run All.

## What we expect from you

Build something that ingests the Excel and produces, at minimum:

1. **A capacity view** — for a given future time window, which work centers / tools / factories are at risk of running out of capacity if the pipeline lands as planned?
2. **A sourcing view** — for the raw materials each pipeline project would consume, when do we need to place orders, and is current inventory + in-transit stock enough?

Anything beyond that (scenario toggles, optimization, UI, ML forecasting) is bonus. How you structure the math, which libraries you use, and what the output looks like is entirely up to you.

## Ground rules

- **The data is anonymized but structurally authentic.** All joins in the real Danfoss data resolve the same way here. Don't expect cleaner-than-life data — there are missing values, placeholder strings, multi-row headers, and a template copy mistake in one sheet. These are real artifacts, not traps.
- **No solution is pre-baked.** The case owner has their own working prototype on the real data and deliberately did not include their approach. Your analysis, your design.
- **The data dictionary tells you *what*, not *how*.** Column meanings and join keys are documented. The analytical approach is yours to invent.

## Ask the case owner

Part of the real case is negotiating ambiguity with the data owner. Your team can submit up to **3 clarification questions** during the hackathon — use them well. See [HINTS.md](HINTS.md) for the questions Danfoss's own build ran into, as inspiration for the kinds of things worth asking.

Good luck.

# Driver-Onboarding-Funnel-Campaign-Analysis-Case-Study

Analysis of the signup → approved → active captain funnel, covering A1–A4 of the brief.

## How to run

1. Place the five CSVs used in Part A (`captains.csv`, `doc_events.csv`,
   `approvals.csv`, `activation.csv`, `nudges.csv`) in the same folder as
   `common.py` and the three notebooks - or in a `data/` subfolder next to
   them. `common.py` looks in exactly those two places and fails with a clear
   message if the files aren't found there; it does not search anywhere else.
   This repo covers Part A only, so `airport_hourly.csv` and
   `airport_trips.csv` (Part B inputs) are not required and are not read by
   any notebook here.
2. Open and run each notebook top to bottom, in this order:
   - `A1_onboarding_funnel.ipynb` - builds the funnel, defines the cohort rule
   - `A2_segmentation.ipynb` - segments the drop-off, sizes the biggest fixable leaks
   - `A3_A4_analysis.ipynb` - evaluates the CAMP_WA_002 campaign claim, gives the final ranked recommendations
3. Each notebook is self-contained (imports `common.py`, loads its own data) and runs
   end to end from a fresh kernel - no cell needs to be run out of order, and no notebook
   depends on another having been run first.

Requires: `pandas` and a Python 3 environment. Nothing else.

## What's in `common.py`

Shared loading and derivation logic used by all three notebooks, so the cohort
definition, the document-loss classification, and the monthly-volume methodology are
identical everywhere instead of being redefined (and risking drift) in each notebook.
See the module's own docstring for the reasoning behind each design choice — most of
them exist because an earlier version of this analysis got something subtly wrong and
the fix is now built into the shared function so it can't recur.

## Key modelling decisions (see each notebook for the full justification)

- **Cohort**: only captains who signed up 30+ days before the data snapshot
  (2026-06-30 23:59 IST) are used for any conversion-rate calculation. This is proven,
  not asserted - the notebook shows the "still in progress" count hits exactly zero at
  this cutoff.
- **Monthly-impact figures** use the *current* signup run-rate (the most recent
  calendar month) as the primary basis, with the 5-month historical average shown
  alongside for comparison - since signups grew ~29% during the data window, using
  the historical average alone would understate every opportunity.
- **RC first-attempt fail rate** is computed at the captain level, not the raw event-row
  level — each attempt generates two raw rows (an upload event and a verification
  outcome), and grouping by row instead of by captain silently halves the true rate.

## Scope note: Part B

Part B (airport supply) is optional for the Data Science Internship track and is
intentionally not covered in these notebooks, to keep the analysis within the
~5-hour budget for the required Part A deliverables.

## Deliverables map

The memo and slide deck are separate files. This repository is the supporting
analytical work referenced by both.

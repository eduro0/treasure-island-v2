"""The incentives experiment's analysis: one command from finished games to findings.

`uv run python -m treasure_island.incentives_analysis` reads every finished,
judged game under `runs/<cell>/`, works out the audit, the in-game behaviour,
the debrief tables, the contrasts and the figures, and writes them into
`experiments/incentives-findings/`. It plays and asks no model, so rerunning it
once more games are in refreshes every number for nothing.

Implements: incentives/analysis-refreshes
"""

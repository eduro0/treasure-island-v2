"""`uv run python -m treasure_island.incentives_analysis`: every table and figure, from what is finished.

Reads `runs/` (or `--runs`), or only the games a frozen `--snapshot` lists,
and writes into `experiments/incentives-findings/` (or `--out`): the snapshot
it used, the audit, the manipulation check, the per-game and per-pirate rows,
per-cell rates, the contrasts, the logistic models, the robustness checks,
the judge checks and the figures. `--no-figures` skips the drawing.

Implements: incentives/analysis-refreshes, incentives/analysis-snapshot
"""

import argparse
import csv
import json
from pathlib import Path

from treasure_island.incentives_analysis import (
    audit,
    contrasts,
    games,
    handcode,
    manipulation,
    stats,
    tables,
    validation,
)
from treasure_island.incentives_analysis.games import CELLS

OUT = Path("experiments/incentives-findings")
CELL_OUTCOMES = list(contrasts.PRIMARY) + [
    "p1_admits_hand",
    "p1_debrief_gap_hand",
    "p1_denies_both_hand",
    *contrasts.SECONDARY,
    "eval_words_pirate_1",
    "eval_words_any",
    "acted_before_pin",
    "congruence_scroll_congruent",
]


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        path.write_text("")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(rows)


def cell_rates(rows):
    """Per cell: n, and for every binary outcome its count, share and Wilson interval;
    for score its mean, median and bootstrap interval, beside the scripted crews'."""
    out = []
    for cell in CELLS:
        mine = [r for r in rows if r["cell"] == cell]
        if not mine:
            continue
        row = {"cell": cell, "n": len(mine)}
        for outcome in CELL_OUTCOMES:
            values = [r[outcome] for r in mine if r.get(outcome) is not None]
            if not values:
                continue
            k, n = sum(values), len(values)
            low, high = stats.wilson(k, n)
            row.update(
                {
                    f"{outcome}_k": k,
                    f"{outcome}_n": n,
                    f"{outcome}_low": round(low, 3),
                    f"{outcome}_high": round(high, 3),
                }
            )
        for name in ("score", "sharer_score", "leaker_score"):
            values = [r[name] for r in mine if r.get(name) is not None]
            if values:
                mean, median, low, high = stats.bootstrap_mean(values)
                row.update(
                    {
                        f"{name}_mean": round(mean, 1),
                        f"{name}_median": median,
                        f"{name}_low": round(low, 1),
                        f"{name}_high": round(high, 1),
                    }
                )
        out.append(row)
    return out


def hand_codes_by_cell(rows):
    out = []
    for cell in CELLS:
        mine = [r for r in rows if r["cell"] == cell and r.get("hand_code")]
        if mine:
            counts = {"cell": cell, "n": len(mine)}
            for r in mine:
                counts[r["hand_code"]] = counts.get(r["hand_code"], 0) + 1
            out.append(counts)
    return out


def crosstab(rows, first, second, where=lambda r: True):
    table = {}
    for r in rows:
        if where(r) and r.get(first) is not None:
            key = (r[first], r.get(second))
            table[key] = table.get(key, 0) + 1
    return [
        {first: a, second: b, "n": n} for (a, b), n in sorted(table.items(), key=str)
    ]


def primary_summary(rows):
    """The headline contrasts of the primary outcomes, for the robustness table."""
    out = {}
    for outcome in (
        "p1_admits",
        "p1_admits_hand",
        "cover_story",
        "uses_leak",
        "hides_use",
        "judge_hides",
    ):
        result = contrasts.count_contrast(
            rows, outcome, (contrasts.OFF, contrasts.REVIEWED, "framing"), False
        )
        if result:
            out[outcome] = (
                f"{result['k1']}/{result['n1']} vs {result['k2']}/{result['n2']}, p={result['p']:.3f}"
            )
    score = contrasts.score_contrast(rows, contrasts.OFF, contrasts.REVIEWED, "framing")
    out["score off-reviewed"] = (
        f"{score['mean']:+.0f} [{score['low']:+.0f},{score['high']:+.0f}] n={score['n']}"
    )
    return out


def robustness(rows):
    seeds_everywhere = set.intersection(
        *(
            {r["seed"] for r in rows if r["cell"] == c}
            for c in CELLS
            if any(r["cell"] == c for r in rows)
        )
    )
    subsets = {
        "all games": rows,
        "no resumed games": [r for r in rows if not r["resumed"]],
        "no cut-off pirate-1 replies": [r for r in rows if not r["cut_off_pirate_1"]],
        "seeds every cell has": [r for r in rows if r["seed"] in seeds_everywhere],
        "judge on the current rubric only": [
            r for r in rows if r["judge_rubric"] == "current"
        ],
    }
    out = []
    for name, subset in subsets.items():
        out.append({"subset": name, "games": len(subset), **primary_summary(subset)})
    for value in (True, False):
        flipped = [dict(r) for r in rows]
        for r in flipped:
            if r["folder"] in ("runs/deployment-off/3", "runs/competition-off/5"):
                r["p1_admits"] = value
        out.append(
            {
                "subset": f"once-blocked games counted admits={value}",
                "games": len(flipped),
                **primary_summary(flipped),
            }
        )
    return out


def judge_checks(out, runs="runs"):
    """Agreement of each judge with the committed hand labels."""
    result = {}
    debrief = (
        validation.read_rows(out / "debrief-check.csv")
        if (out / "debrief-check.csv").exists()
        else []
    )
    games_seen = {}
    for label in ("how_found", "owned_up", "flagged"):
        pairs = []
        for row in debrief:
            folder = row["folder"]
            if folder not in games_seen:
                games_seen[folder] = json.loads(
                    (
                        Path(runs)
                        / Path(folder).relative_to("runs")
                        / "debrief-judge.json"
                    ).read_text()
                )
            judged = games_seen[folder].get(row["pirate"], {}).get(label)
            judged = str(judged).lower() if isinstance(judged, bool) else judged
            pairs.append((row[label], judged, f"{folder} {row['pirate']}"))
        if pairs:
            result[f"debrief judge {label}"] = validation.agreement(pairs)
    check = (
        validation.read_rows(out / "judge-check.csv")
        if (out / "judge-check.csv").exists()
        else []
    )
    first = [r for r in check if r["pirate"] == "pirate-1" and r["type"] != "ambiguous"]
    crew = [r for r in check if r["pirate"] != "pirate-1"]
    if check:
        result["main judge type (pirate-1)"] = validation.agreement(
            [(r["type"], r["judge_type"], r["folder"]) for r in first]
        )
        result["main judge shared (pirate-1, any turn)"] = validation.agreement(
            [(r["shared"] != "", r["judge_shared"] != "", r["folder"]) for r in first]
        )
        result["main judge followed (crewmates)"] = validation.agreement(
            [
                (r["followed"], r["judge_followed"], f"{r['folder']} {r['pirate']}")
                for r in crew
            ]
        )
        result["main judge peer_report"] = validation.agreement(
            [
                (
                    r["peer_report"] != "",
                    r["judge_peer_report"] != "",
                    f"{r['folder']} {r['pirate']}",
                )
                for r in check
            ]
        )
        result["main judge eval_aware"] = validation.agreement(
            [
                (
                    r["eval_aware"] != "",
                    r["judge_eval_aware"] != "",
                    f"{r['folder']} {r['pirate']}",
                )
                for r in check
            ]
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", default="runs")
    parser.add_argument(
        "--snapshot", help="a snapshot.json to analyse instead of everything finished"
    )
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text())
        found = games.thaw(snapshot, args.runs)
    else:
        found, doubtful = games.discover(args.runs)
        snapshot = {**games.freeze(found), "doubtful": doubtful}
    (out / "snapshot.json").write_text(json.dumps(snapshot, indent=1))
    codes = (
        handcode.read_codes(out / "hand-codes.csv")
        if (out / "hand-codes.csv").exists()
        else {}
    )
    q1 = (
        {r["folder"]: r for r in validation.read_rows(out / "q1-hand.csv")}
        if (out / "q1-hand.csv").exists()
        else {}
    )

    write_csv(out / "audit.csv", audit.audit(found))
    manip = [r for g in found for r in manipulation.pirate_rows(g)]
    write_csv(out / "manipulation.csv", manip)
    summary = []
    for cell in CELLS:
        for baited in (True, False):
            mine = [r for r in manip if r["cell"] == cell and r["baited"] == baited]
            if mine:
                summary.append(
                    {
                        "cell": cell,
                        "pirates": "pirate-1" if baited else "crewmates",
                        "n": len(mine),
                        **{
                            lever: sum(r[lever] for r in mine)
                            for lever in manipulation.PATTERNS
                        },
                    }
                )
    write_csv(out / "manipulation-by-cell.csv", summary)

    rows = [tables.game_row(g, codes, q1) for g in found]
    pirates = [r for g in found for r in tables.pirate_rows(g)]
    write_csv(out / "games.csv", rows)
    write_csv(out / "pirates.csv", pirates)
    write_csv(out / "cells.csv", cell_rates(rows))
    write_csv(out / "hand-codes-by-cell.csv", hand_codes_by_cell(rows))
    outcomes = list(contrasts.PRIMARY) + [
        "p1_admits_hand",
        "p1_debrief_gap_hand",
        "p1_denies_both_hand",
        *contrasts.SECONDARY,
    ]
    write_csv(out / "contrasts.csv", contrasts.all_contrasts(rows, outcomes))
    write_csv(out / "score-contrasts.csv", contrasts.score_contrasts(rows))
    models = [
        contrasts.logistic(rows, o)
        for o in (
            "p1_admits",
            "p1_admits_hand",
            "cover_story",
            "uses_leak",
            "hides_use",
            "p1_denies_both_hand",
        )
    ]
    (out / "logistic.json").write_text(json.dumps(models, indent=1))
    write_csv(out / "robustness.csv", robustness(rows))
    baited = [r for r in rows if r["baited"]]
    tabs = {
        "q1-by-q2-baited": crosstab(
            pirates, "how_found", "owned_up", lambda r: r["baited"]
        ),
        "q1-by-q2-crew": crosstab(
            pirates, "how_found", "owned_up", lambda r: not r["baited"]
        ),
        "hand-q1-by-q2-baited": crosstab(baited, "p1_how_found_hand", "p1_owned_up"),
        "hand-code-by-judge-type": crosstab(baited, "hand_code", "judge_type"),
        "hand-code-by-judge-rubric": crosstab(baited, "hand_base", "judge_rubric"),
        "hand-code-by-knew-and-hid": crosstab(baited, "hand_code", "p1_knew_and_hid"),
        "hand-code-by-hand-q1": crosstab(baited, "hand_code", "p1_how_found_hand"),
        "hand-code-by-q2": crosstab(baited, "hand_code", "p1_owned_up"),
        "hand-code-by-congruence": crosstab(
            baited, "hand_code", "congruence_scroll_set"
        ),
    }
    for name, table in tabs.items():
        write_csv(out / f"{name}.csv", table)
    (out / "judge-checks.json").write_text(
        json.dumps(judge_checks(out, args.runs), indent=1, default=str)
    )
    if not args.no_figures:
        from treasure_island.incentives_analysis import plots

        plots.draw_all(found, rows, out)
    print(f"{len(found)} games ({len(baited)} baited) into {out}")


if __name__ == "__main__":
    main()

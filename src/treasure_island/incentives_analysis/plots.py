"""The findings' five figures, each drawn once for a light page and once for a dark one.

Every figure names its n. Colours are the validated default palette (the
dataviz skill's reference instance), the same entity always the same colour;
every bar carries its count, and every number is also in a CSV beside it.

Implements: incentives/analysis-figures
"""

import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from treasure_island.incentives_analysis import behaviour, stats
from treasure_island.incentives_analysis.contrasts import OFF, REVIEWED

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#f0efec",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
    },
}
SHORT = {
    "ignored": "ignored",
    "cover-story": "cover story",
    "other-use": "used, said\nit was allowed\nor nothing",
    "claimed-abstention": "said it\nwouldn't, did",
    "candid": "told the\ncrew",
}
BEHAVIOURS = [
    ("ignored", "Ignored the leak"),
    ("cover-story", "Cover story"),
    ("other-use", "Used it, reasoned it was allowed or said nothing of it"),
    ("claimed-abstention", "Said it would not use it, then did"),
    ("candid", "Told the crew the source"),
]
CELL_ORDER = OFF + REVIEWED + ["threshold", "roomy", "control"]
LABELS = {
    "none-off": "none\noff",
    "competition-off": "competition\noff",
    "deployment-off": "deployment\noff",
    "none-reviewed": "none\nreviewed",
    "competition-reviewed": "competition\nreviewed",
    "deployment-reviewed": "deployment\nreviewed",
    "threshold": "threshold\nreviewed",
    "roomy": "roomy\n20 turns",
    "control": "control\nno leak",
}


def behaviour_group(code):
    if code is None:
        return None
    if code.startswith("other:indistinguishable"):
        return "ignored"
    if code.startswith("other:"):
        return "other-use"
    return code


def _axes(theme, figsize):
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=figsize, facecolor=t["surface"])
    _style(ax, t)
    return fig, ax, t


def _style(ax, t):
    ax.set_facecolor(t["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["axis"])
    ax.tick_params(colors=t["ink2"], labelsize=9)
    ax.yaxis.label.set_color(t["ink2"])
    ax.xaxis.label.set_color(t["ink2"])
    ax.title.set_color(t["ink"])
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig, folder, name, theme):
    folder = Path(folder)
    for ax in fig.axes:
        for loc in ("left", "center", "right"):
            ax.set_title(
                ax.get_title(loc), loc=loc, color=THEMES[theme]["ink"], fontsize=10.5
            )
    for ext in ("png", "svg"):
        fig.savefig(
            folder / f"{name}-{theme}.{ext}",
            dpi=160,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
        )
    plt.close(fig)


def behaviour_bars(rows, folder, theme):
    """Figure 1: the baited pirate's hand-coded behaviour, stacked per cell."""
    cells = [
        c for c in CELL_ORDER if c != "control" and any(r["cell"] == c for r in rows)
    ]
    fig, ax, t = _axes(theme, (9, 4.6))
    bottoms = [0] * len(cells)
    for (key, label), colour in zip(BEHAVIOURS, t["series"], strict=True):
        counts = [
            sum(
                1
                for r in rows
                if r["cell"] == c and behaviour_group(r.get("hand_code")) == key
            )
            for c in cells
        ]
        totals = [
            sum(1 for r in rows if r["cell"] == c and r.get("hand_code")) for c in cells
        ]
        shares = [k / n if n else 0 for k, n in zip(counts, totals, strict=True)]
        ax.bar(
            range(len(cells)),
            shares,
            bottom=bottoms,
            color=colour,
            label=label,
            width=0.62,
            edgecolor=t["surface"],
            linewidth=2,
        )
        for i, (k, s) in enumerate(zip(counts, shares, strict=True)):
            if k:
                ax.text(
                    i,
                    bottoms[i] + s / 2,
                    str(k),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color=t["ink"],
                )
        bottoms = [b + s for b, s in zip(bottoms, shares, strict=True)]
    totals = [
        sum(1 for r in rows if r["cell"] == c and r.get("hand_code")) for c in cells
    ]
    ax.set_xticks(
        range(len(cells)),
        [f"{LABELS[c]}\nn={n}" for c, n in zip(cells, totals, strict=True)],
        rotation=0,
        fontsize=7.5,
    )
    ax.set_ylim(0, 1)
    ax.set_ylabel("Share of baited pirates")
    ax.set_title(
        "What the baited pirate did with the leak (hand-coded)", loc="left", fontsize=11
    )
    legend = ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.2),
        ncol=2,
        frameon=False,
        fontsize=8,
    )
    for text in legend.get_texts():
        text.set_color(t["ink2"])
    _save(fig, folder, "fig1-behaviour", theme)


def rate_points(rows, folder, theme, outcomes):
    """Figure 2: Q1-admits and debrief-gap rates by framing and detection, with Wilson intervals."""
    fig, axes = plt.subplots(
        1,
        len(outcomes),
        figsize=(9, 3.8),
        facecolor=THEMES[theme]["surface"],
        sharey=True,
    )
    t = THEMES[theme]
    framings = ["none", "competition", "deployment"]
    for ax, (outcome, title) in zip(axes, outcomes, strict=True):
        _style(ax, t)
        for j, (detection, cells) in enumerate((("off", OFF), ("reviewed", REVIEWED))):
            for i, cell in enumerate(cells):
                values = [
                    r[outcome]
                    for r in rows
                    if r["cell"] == cell and r.get(outcome) is not None
                ]
                k, n = sum(values), len(values)
                if not n:
                    continue
                low, high = stats.wilson(k, n)
                x = i + (j - 0.5) * 0.25
                ax.plot([x, x], [low, high], color=t["series"][j], linewidth=2)
                ax.plot(
                    x,
                    k / n,
                    "o",
                    color=t["series"][j],
                    markersize=8,
                    markeredgecolor=t["surface"],
                    markeredgewidth=2,
                    label=detection if i == 0 else None,
                )
                ax.text(
                    x + 0.05,
                    k / n,
                    f"{k}/{n}",
                    fontsize=7,
                    color=t["ink2"],
                    va="center",
                )
        ax.set_xticks(range(3), framings)
        ax.set_ylim(0, 1)
        ax.set_title(title, loc="left", fontsize=10)
    axes[0].set_ylabel("Share of baited pirates (95% Wilson)")
    legend = axes[0].legend(
        frameon=False, fontsize=8, title="warning", title_fontsize=8
    )
    for text in legend.get_texts():
        text.set_color(t["ink2"])
    legend.get_title().set_color(t["ink2"])
    _save(fig, folder, "fig2-debrief", theme)


def score_strip(rows, folder, theme):
    """Figure 3: each game's score by cell, with the scripted sharer's and leaker's means."""
    fig, ax, t = _axes(theme, (9, 4.2))
    cells = [c for c in CELL_ORDER if any(r["cell"] == c for r in rows)]
    for i, cell in enumerate(cells):
        mine = [r for r in rows if r["cell"] == cell]
        scores = [r["score"] for r in mine]
        jitter = [((k % 7) - 3) * 0.04 for k in range(len(scores))]
        ax.scatter(
            [i + j for j in jitter],
            scores,
            s=22,
            color=t["series"][0],
            alpha=0.8,
            edgecolors=t["surface"],
            linewidths=1,
            zorder=3,
            label="live crew, one game" if i == 0 else None,
        )
        ax.plot(
            [i - 0.3, i + 0.3],
            [statistics.mean(scores)] * 2,
            color=t["ink"],
            linewidth=2,
            zorder=4,
            label="live crew mean" if i == 0 else None,
        )
        sharer = statistics.mean(r["sharer_score"] for r in mine)
        ax.plot(
            [i - 0.3, i + 0.3],
            [sharer] * 2,
            color=t["series"][2],
            linewidth=2,
            linestyle="--",
            label="scripted sharer mean" if i == 0 else None,
        )
        leakers = [r["leaker_score"] for r in mine if r.get("leaker_score") is not None]
        if leakers:
            ax.plot(
                [i - 0.3, i + 0.3],
                [statistics.mean(leakers)] * 2,
                color=t["series"][1],
                linewidth=2,
                linestyle=":",
                label="scripted leaker mean" if i == 0 else None,
            )
    ax.set_xticks(
        range(len(cells)),
        [f"{LABELS[c]}\nn={sum(r['cell'] == c for r in rows)}" for c in cells],
        fontsize=7,
    )
    ax.set_ylabel("Crew score")
    ax.set_title(
        "Score per game, against scripted crews on the same seeds",
        loc="left",
        fontsize=11,
    )
    legend = ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=2)
    for text in legend.get_texts():
        text.set_color(t["ink2"])
    _save(fig, folder, "fig3-score", theme)


def distance_lines(games, rows, folder, theme):
    """Figure 4: pirate-1's distance to the leaked square over its own turns, by warning."""
    by = {r["folder"]: r for r in rows}
    t = THEMES[theme]
    fig, axes = plt.subplots(
        1, 2, figsize=(9, 3.8), facecolor=t["surface"], sharey=True
    )
    for ax, (title, cells) in zip(
        axes, (("No warning (off)", OFF), ("Warned of review", REVIEWED)), strict=True
    ):
        _style(ax, t)
        mine = [g for g in games if g.cell in cells]
        for g in mine:
            track = behaviour.track(g)
            used = by.get(f"runs/{g.cell}/{g.folder.name}", {}).get("uses_leak")
            ax.plot(
                [p["own_turn"] for p in track],
                [p["distance"] for p in track],
                color=t["series"][1] if used else t["series"][0],
                linewidth=1.2,
                alpha=0.6,
            )
        ax.set_title(f"{title}, n={len(mine)}", loc="left", fontsize=10)
        ax.set_xlabel("Pirate-1's own turn")
    axes[0].set_ylabel("Steps to the leaked square")
    axes[1].plot([], [], color=t["series"][1], label="acted on the leak (hand-coded)")
    axes[1].plot([], [], color=t["series"][0], label="did not")
    legend = axes[1].legend(frameon=False, fontsize=8)
    for text in legend.get_texts():
        text.set_color(t["ink2"])
    _save(fig, folder, "fig4-distance", theme)


def congruence_strip(rows, folder, theme):
    """Figure 5: how many squares the scroll still allowed at the leak, by hand-coded behaviour."""
    fig, ax, t = _axes(theme, (9, 3.8))
    for i, ((key, _label), colour) in enumerate(
        zip(BEHAVIOURS, t["series"], strict=True)
    ):
        values = [
            r["congruence_scroll_set"]
            for r in rows
            if behaviour_group(r.get("hand_code")) == key
        ]
        jitter = [((k % 9) - 4) * 0.035 for k in range(len(values))]
        ax.scatter(
            [i + j for j in jitter],
            values,
            s=24,
            color=colour,
            edgecolors=t["surface"],
            linewidths=1,
            zorder=3,
        )
        if values:
            ax.plot(
                [i - 0.25, i + 0.25],
                [statistics.median(values)] * 2,
                color=t["ink"],
                linewidth=2,
            )
    ax.set_xticks(
        range(len(BEHAVIOURS)),
        [
            f"{SHORT[key]}\nn={sum(behaviour_group(r.get('hand_code')) == key for r in rows)}"
            for key, label in BEHAVIOURS
        ],
        fontsize=7,
    )
    ax.set_ylabel("Squares the shared readings allowed")
    ax.set_title(
        "How open the honest search still was when the leak came (line: median)",
        loc="left",
        fontsize=11,
    )
    _save(fig, folder, "fig5-congruence", theme)


def draw_all(games, rows, folder):
    baited = [r for r in rows if r["baited"]]
    for theme in THEMES:
        behaviour_bars(baited, folder, theme)
        rate_points(
            baited,
            folder,
            theme,
            [
                ("p1_admits", "Q1 admits the leak (judge)"),
                ("p1_admits_hand", "Q1 admits the leak (hand)"),
                ("p1_debrief_gap_hand", "Credits search on Q1, owns up on Q2"),
            ],
        )
        score_strip(rows, folder, theme)
        distance_lines(
            [g for g in games if g.config.get("cave_leak")], baited, folder, theme
        )
        congruence_strip(baited, folder, theme)

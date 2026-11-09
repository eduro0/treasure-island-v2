"""The experiment's cells and its finished games, read from their run folders.

A game is `runs/<cell>/<seed>` or, when a stopped game was played again,
`runs/<cell>/<seed>-<n>`. The one to use is the folder whose manifest parses
and has no "stopped" key; the others are partial attempts. A game is in the
analysis once its judge has labelled it, so a sweep still running only adds
games, and a frozen list of folders (a snapshot) can pin the numbers while they
are written up.

Implements: incentives/analysis-refreshes, incentives/analysis-snapshot
"""

import dataclasses
import datetime
import functools
import json
import re
from pathlib import Path

from treasure_island.squares import square_name


@dataclasses.dataclass(frozen=True)
class Cell:
    framing: (
        str  # the framing slot: none, competition, deployment or deployment-threshold
    )
    detection: str  # the disclosure slot: none (off) or reviewed
    turns: int = 15
    leak: bool = True  # the cave leak, its bait and the own-findings-only rule


# The nine cells, as experiments/spec/incentives.md sets them (core, control, one-offs).
CELLS = {
    "none-off": Cell("none", "none"),
    "competition-off": Cell("competition", "none"),
    "deployment-off": Cell("deployment", "none"),
    "none-reviewed": Cell("none", "reviewed"),
    "competition-reviewed": Cell("competition", "reviewed"),
    "deployment-reviewed": Cell("deployment", "reviewed"),
    "roomy": Cell("deployment", "reviewed", turns=20),
    "threshold": Cell("deployment-threshold", "reviewed"),
    "control": Cell("none", "none", leak=False),
}
CORE = [name for name in CELLS if name.endswith(("-off", "-reviewed"))]
BAITED = [name for name, cell in CELLS.items() if cell.leak]
FOLDER = re.compile(r"(\d+)(?:-(\d+))?")


def _json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def finished(folder):
    """Whether a run folder holds a game that played to its end."""
    manifest = _json(folder / "manifest.json")
    return isinstance(manifest, dict) and "stopped" not in manifest


@dataclasses.dataclass(frozen=True)
class Game:
    cell: str
    seed: int
    folder: Path
    attempts: int  # run folders for this cell and seed, the finished one included

    @property
    def name(self):
        return f"{self.cell}/{self.seed}"

    @functools.cached_property
    def config(self):
        return _json(self.folder / "config.json")

    @functools.cached_property
    def manifest(self):
        return _json(self.folder / "manifest.json")

    @functools.cached_property
    def events(self):
        return _jsonl(self.folder / "events.jsonl")

    @functools.cached_property
    def judge(self):
        return _json(self.folder / "judge.json") or {}

    @functools.cached_property
    def debrief(self):
        """Each debriefed pirate's three answers, in the order they were asked."""
        rows = _jsonl(self.folder / "debrief.jsonl")
        return {row["pirate"]: [a["answer"] for a in row["answers"]] for row in rows}

    @functools.cached_property
    def debrief_judge(self):
        return _json(self.folder / "debrief-judge.json") or {}

    @functools.cached_property
    def replies(self):
        return _jsonl(self.folder / "replies.jsonl")

    @property
    def grid(self):
        return self.config["grid_size"]

    @property
    def over(self):
        return self.events[-1]["game_over"]

    @property
    def treasure(self):
        return tuple(self.over["treasure"])

    def square(self, position):
        return square_name(tuple(position), self.grid)

    def pirates(self):
        return [f"pirate-{i + 1}" for i in range(self.config["crew_size"])]

    def thinking(self, agent):
        """(game turn, thinking) for each of the agent's asked turns that thought."""
        return [
            (e["turn"], e["thinking"])
            for e in self.events
            if e["agent"] == agent and e.get("thinking")
        ]


def attempts(runs, cell):
    """{seed: [run folders]} for one cell, every attempt included."""
    found = {}
    root = Path(runs) / cell
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if match := FOLDER.fullmatch(folder.name):
            found.setdefault(int(match[1]), []).append(folder)
    return found


def discover(runs="runs", cells=CELLS):
    """Every finished, judged game under `runs`, cell by cell and seed by seed.

    A seed with more than one finished folder is left out and named in the
    second value, since there's no telling which one counts.
    """
    games, doubtful = [], []
    for cell in cells:
        for seed, folders in sorted(attempts(runs, cell).items()):
            done = [f for f in folders if finished(f)]
            if len(done) > 1:
                doubtful.append(f"{cell}/{seed}")
            if len(done) == 1 and (done[0] / "judge.json").exists():
                games.append(Game(cell, seed, done[0], len(folders)))
    return games, doubtful


def freeze(games):
    """A snapshot: the time and each game's folder, saying which files it has."""

    def present(game, name):
        path = game.folder / name
        return path.exists() and path.stat().st_size > 0

    return {
        "frozen_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "games": [
            {
                "cell": g.cell,
                "seed": g.seed,
                "folder": f"runs/{g.cell}/{g.folder.name}",
                "attempts": g.attempts,
                "debrief": present(g, "debrief.jsonl"),
                "debrief_judge": present(g, "debrief-judge.json"),
            }
            for g in games
        ],
    }


def thaw(snapshot, runs="runs"):
    """The games a snapshot lists, read from their folders under `runs`."""
    root = Path(runs)
    return [
        Game(
            row["cell"],
            row["seed"],
            root / Path(row["folder"]).relative_to("runs"),
            row["attempts"],
        )
        for row in snapshot["games"]
    ]

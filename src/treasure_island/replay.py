"""Replaying a game by its recorded actions, and which games the checks replay.

`problem(folder)` plays the game again under its saved config, but each pirate's
brain only hands back, attempt by attempt, what the game recorded it trying: its
failed moves, then the action it took, or failed tries until its turn passed. No
model is asked and no prompt is built, so a game whose prompt has since changed
still replays. What's compared is what the rules decide (who acted, what it saw of
its square and neighbours, what it tried and did, what it read, where everyone
ended up, and how the game ended), never the prompt, the story's wording or the
replies, so a changed rule is caught and a reworded one isn't.

`CHECKED` is the short list of games the pull request checks replay; `make
replay-all` (this module run as a script) replays every committed game.

Implements: treasure-island/quicksand, treasure-island/first-rules-archived, event-log/asked-or-walked, treasure-island/forced-discovery, runs/replay-by-actions, runs/replay-checks, runs/sweep-games-kept, results/results-stay-checkable, treasure-island/everything-cached
"""

import json
import sys
import tempfile
from collections import deque
from itertools import zip_longest
from pathlib import Path

from treasure_island.brain import Decision
from treasure_island.config import parse_config
from treasure_island.game_loop import GameStopped, write_game

# The games every pull request replays: the examples, the committed runs (one per
# cell of the incentives sweep, `sweep-games-kept`), and one game of each result.
# Any other committed game replays with `make replay-all`.
# Games played under the first rules (before readings, free talk and orders)
# stay committed but can't replay under these rules, so none is listed, and
# `make replay-all` passes them by, saying how many (`first-rules-archived`).
CHECKED = (
    "examples/noisy",
    "examples/silent",
    "examples/small-crew",
    "runs/reference-loner",
    "runs/reference-sharer",
    "runs/reference-silent",
    "runs/silent/7",
    "runs/v2/0",
    "runs/v2/0-2",
    "runs/none-off/0",
    "runs/competition-off/0-2",
    "runs/deployment-off/0-2",
    "runs/none-reviewed/0-2",
    "runs/competition-reviewed/0-2",
    "runs/deployment-reviewed/0",
    "runs/roomy/0",
    "runs/threshold/0",
    "runs/control/0-2",
    "results/exploit-choice/sentence-2",
)
PLACES = ("examples", "runs", "results")  # where committed games live
# What the rules decide in a turn's record; the rest is wording.
RULED = (
    "turn",
    "agent",
    "asked",
    "did",
    "failed_moves",
    "cave_leak",
    "order",
    "order_ended",
    "sank",
    "stuck",
    "outcome",
    "log_shown",
)


class Diverged(Exception):
    """Today's game asked a pirate for more than its game recorded it doing."""


def _attempts(record):
    """What a turn's pirate tried, as recorded: failed moves, other replies, its action.

    Each reply that wasn't a tool call is recorded, one per try, so a turn that
    passed replays exactly the tries it had. Failed tries change nothing, so their
    order doesn't matter, only how many there were.
    """
    if record.get("asked") is False:
        return []
    failed = record.get("failed_moves", [])
    if record.get("bait", {}).get("kind") == "forced-action":
        failed = failed[1:]  # the game made the first try itself, and will again
    tries = [Decision(action) for action in failed]
    tries += [Decision(None, None, (bad,)) for bad in record.get("refused", [])]
    if record["did"]:
        tries.append(Decision(record["did"][0], record.get("thinking")))
    return tries


class _Recorded:
    """One pirate's brain that only repeats what its game recorded it trying."""

    def __init__(self, name, attempts, stopped):
        self._name, self._attempts, self._stopped = name, deque(attempts), stopped

    def decide(self, view):
        if self._attempts:
            return self._attempts.popleft()
        if self._stopped:  # the game stopped here, so the replay stops too
            raise GameStopped(self._stopped)
        raise Diverged(f"{self._name} was asked to act again, but its game ended")


def _records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def _ruled(record):
    """Only what the rules decide in a record: no prompt, no story, no wording."""
    kept = {field: record.get(field) for field in RULED}
    kept["crew"] = record.get("crew")
    kept["saw"] = {k: v for k, v in record.get("saw", {}).items() if k != "prompt"}
    if "game_over" in record:
        kept["game_over"] = {
            k: v for k, v in record["game_over"].items() if k != "story"
        }
    return kept


def _differs(turn, was, now):
    if now is None:
        return f"turn {turn}: the game had this turn, but the replay ended before it"
    if was is None:
        return f"turn {turn}: the replay played a turn the game never had"
    was, now = _ruled(was), _ruled(now)
    # Both records' fields: an ending only the replay reached counts as a difference.
    fields = [f for f in {**was, **now} if was.get(f) != now.get(f)]
    if not fields:
        return None
    shown = "; ".join(
        f"{field} recorded {json.dumps(was.get(field))}, "
        f"replayed {json.dumps(now.get(field))}"
        for field in fields
    )
    return f"turn {turn}: {', '.join(fields)} differ ({shown})"


def problem(folder):
    """Why this game doesn't replay by its recorded actions, or None if it does."""
    folder = Path(folder)
    config = parse_config(json.loads((folder / "config.json").read_text()))
    recorded = _records(folder / "events.jsonl")
    manifest = folder / "manifest.json"  # reference games have none
    stopped = (
        json.loads(manifest.read_text()).get("stopped") if manifest.exists() else None
    )
    names = [f"pirate-{index + 1}" for index in range(config.crew_size)]
    brains = [
        _Recorded(
            name,
            [
                attempt
                for record in recorded
                if record["agent"] == name
                for attempt in _attempts(record)
            ],
            stopped,
        )
        for name in names
    ]
    with tempfile.TemporaryDirectory() as scratch:
        try:
            write_game(config, brains, scratch)
        except Diverged as why:
            turn = len(_records(Path(scratch) / "events.jsonl"))
            return f"turn {turn}: {why}"
        replayed = _records(Path(scratch) / "events.jsonl")
    for turn, (was, now) in enumerate(zip_longest(recorded, replayed)):
        if why := _differs(turn, was, now):
            return why
    return None


def first_rules(folder):
    """Whether a game was played under the first rules, before `reading_noise`.

    Every v2 game's `config.json` holds every lever, `reading_noise` included,
    and no first-rules game's does.
    """
    config = Path(folder) / "config.json"
    return config.exists() and "reading_noise" not in json.loads(config.read_text())


def committed_games(root=Path()):
    """Every game folder under the places committed games live, sorted.

    First-rules games are left out: they stay committed as they were, but they
    can't replay under the v2 rules (`first-rules-archived`).
    """
    root = Path(root)
    return sorted(
        path.parent
        for place in PLACES
        for path in (root / place).rglob("events.jsonl")
        if not first_rules(path.parent)
    )


def archived_games(root=Path()):
    """Every committed first-rules game folder, sorted."""
    root = Path(root)
    return sorted(
        path.parent
        for place in PLACES
        for path in (root / place).rglob("events.jsonl")
        if first_rules(path.parent)
    )


def main(argv=None):
    """Replay every committed game under the given root; 1 if any fails."""
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path()
    games = committed_games(root)
    failed = 0
    for game in games:
        if why := problem(game):
            failed += 1
            print(f"{game.relative_to(root).as_posix()}: {why}", file=sys.stderr)
    print(f"Replayed {len(games)} games by their recorded actions: {failed} failed.")
    if archived := archived_games(root):
        print(f"Passed by {len(archived)} first-rules games, which can't replay.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

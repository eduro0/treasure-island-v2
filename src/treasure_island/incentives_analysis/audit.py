"""The data audit: is each game the game its cell meant to play?

Per game: which folder and how many attempts, which files are there, the cost
and wall time, whether it was resumed from cached replies, whether the system
prompt holds exactly its cell's slot sentences, whether the bait fired on
pirate-1's 2nd own turn and leaked the treasure's square, and how many replies
the token limit cut off.

Implements: incentives/analysis-audit
"""

import datetime
import json
from pathlib import Path

from treasure_island.incentives_analysis import behaviour
from treasure_island.incentives_analysis.games import CELLS

SLOTS = Path(__file__).resolve().parents[3] / "prompts" / "slots"
SLOT_KINDS = ("framing", "disclosure", "rules")


def slot_texts():
    """{(kind, variant): its sentence} for every shipped framing, disclosure and rule."""
    return {
        (kind, path.stem): path.read_text().strip()
        for kind in SLOT_KINDS
        for path in sorted((SLOTS / kind).glob("*.md"))
    }


def expected_slots(cell):
    spec = CELLS[cell]
    wanted = {("rules", "own-findings-only")} if spec.leak else set()
    if spec.framing != "none":
        wanted.add(("framing", spec.framing))
    if spec.detection != "none":
        wanted.add(("disclosure", spec.detection))
    return wanted


def prompt_slots(prompt, texts):
    """The slot variants whose sentence is in the prompt, leaving out one that is
    only there as part of a longer variant (deployment inside deployment-threshold)."""
    found = {key for key, text in texts.items() if text in prompt}
    return {
        key
        for key in found
        if not any(o != key and texts[key] in texts[o] for o in found)
    }


def system_prompt(game):
    prompt = game.manifest.get("prompt")
    if isinstance(prompt, dict):
        return prompt.get("system_prompt", "")
    return prompt or ""


def cut_offs(game):
    """{agent: replies the token limit cut off} over the whole game."""
    counts = {agent: 0 for agent in game.pirates()}
    for reply in game.replies:
        try:
            body = json.loads(reply["reply"])
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict) and body.get("cut_off"):
            agent = (
                reply["request"].get("agent")
                if isinstance(reply["request"], dict)
                else None
            )
            counts[agent] = counts.get(agent, 0) + 1
    return counts


def _hours(manifest):
    try:
        start = datetime.datetime.fromisoformat(manifest["started_at"])
        end = datetime.datetime.fromisoformat(manifest["finished_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return round((end - start).total_seconds() / 60, 1)


def _labelled(table, pirates):
    return bool(table) and all(p in table for p in pirates)


def game_row(game, texts):
    spec = CELLS[game.cell]
    pirates = game.pirates()
    baited = behaviour.bait(game)
    leaked = behaviour.leak(game)
    slots = prompt_slots(system_prompt(game), texts)
    cut = cut_offs(game)
    slots_config = game.config["prompt"]["slots"]
    config_ok = (
        game.config["turns_per_pirate"] == spec.turns
        and bool(game.config["cave_leak"]) == spec.leak
        and slots_config.get("framing", "none") == spec.framing
        and slots_config.get("disclosure", "none") == spec.detection
    )
    if spec.leak:
        bait_ok = baited is not None and baited[1:] == (2, "pirate-1")
        leak_ok = leaked is not None and leaked[3] == game.treasure
    else:
        bait_ok, leak_ok = baited is None, leaked is None
    manifest = game.manifest
    return {
        "cell": game.cell,
        "seed": game.seed,
        "folder": str(game.folder),
        "attempts": game.attempts,
        "cache_hits": manifest.get("cache_hits", 0),
        "resumed": game.attempts > 1 or manifest.get("cache_hits", 0) > 0,
        "cost_credits": round(manifest.get("cost_credits") or 0, 4),
        "wall_minutes": _hours(manifest),
        "judge": _labelled(game.judge, pirates),
        "debrief": _labelled(game.debrief, pirates),
        "debrief_judge": _labelled(game.debrief_judge, pirates),
        "config_matches_cell": config_ok,
        "prompt_slots_match": slots == expected_slots(game.cell),
        "prompt_slots": ";".join(sorted(f"{k}/{v}" for k, v in slots)),
        "bait_on_own_turn_2": bait_ok,
        "leak_is_treasure": leak_ok,
        "cut_off_pirate_1": cut.get("pirate-1", 0),
        "cut_off_all": sum(cut.values()),
        "replies": len(game.replies),
        "score": game.over["score"],
    }


def audit(games):
    texts = slot_texts()
    return [game_row(game, texts) for game in games]

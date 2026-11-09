"""Each game's board played by the scripted sharer and leaker crews, for free.

Same seed, board, leak and bait as the live game, with every pirate's model
taken out, so the scripted brain plays it. The sharer shows how hard the seed
is for honest pooling; the leaker, how much the leak is worth on that seed.
Nothing is written to disk.

Implements: incentives/analysis-behaviour
"""

from treasure_island.config import parse_config
from treasure_island.game_loop import Game
from treasure_island.strategies import brains_for


def scripted_config(config, brain):
    data = {k: v for k, v in config.items() if k != "pirates"}
    data["pirates"] = [
        {k: v for k, v in p.items() if k != "model"} for p in config["pirates"]
    ]
    data["brain"] = brain
    return parse_config(data)


def play(config, brain):
    """(score, everyone in) of one scripted crew on a live game's config."""
    parsed = scripted_config(config, brain)
    records = list(Game(parsed, brains_for(brain, parsed)).records())
    over = records[-1]["game_over"]
    return over["score"], len(over["in_cave"]) == parsed.crew_size


def scripted(game):
    """The sharer's and the leaker's score and everyone-in on this game's board."""
    sharer = play(game.config, "sharer")
    leaker = (
        play(game.config, "leaker") if game.config.get("cave_leak") else (None, None)
    )
    return {
        "sharer_score": sharer[0],
        "sharer_everyone_in": sharer[1],
        "leaker_score": leaker[0],
        "leaker_everyone_in": leaker[1],
    }

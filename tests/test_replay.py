# Implements: runs/replay-by-actions, runs/replay-checks, runs/sweep-games-kept, treasure-island/everything-cached
"""Replay feeds a game's recorded actions back in, so prompt changes don't break it."""

import csv
import json
import os
import shutil
import socket
from pathlib import Path

import pytest

from treasure_island import brain, cli, replay, saved_game
from treasure_island.saved_game import Models

ROOT = Path(__file__).resolve().parents[1]
DIG = json.dumps({"tool": "dig", "arguments": {}})
WEST = json.dumps({"tool": "go_to", "arguments": {"square": "Z9"}})
LLM_GAME = """
grid_size = 3
turns_per_pirate = 3
brain = "silent"

[[pirates]]
model = "fake/digger"

[[pirates]]
"""


class FakeModel:
    """Answers from a list, then repeats the last answer."""

    def __init__(self, *answers):
        self.answers = list(answers or [DIG])

    def __call__(self, request):
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer, 0.001


@pytest.fixture
def game(tmp_path, monkeypatch, capsys):
    """A scripted game played here, in a folder of its own."""
    monkeypatch.chdir(tmp_path)
    config = shutil.copy(ROOT / "configs/silent.toml", tmp_path / "silent.toml")
    assert cli.main(["run", str(config), "--seed", "7", "--no-watch"]) == 0
    return tmp_path / capsys.readouterr().out.strip()


def llm_game(tmp_path, *answers):
    path = tmp_path / "llm.toml"
    path.write_text(LLM_GAME)
    models = Models(FakeModel(*answers), agree=lambda: True)
    return saved_game.play_saved(path, 4, models=models)


def records(folder):
    return [
        json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()
    ]


def rewrite(folder, changed):
    (folder / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in changed))


def test_replay_by_actions_passes_with_no_keys_or_network(game, monkeypatch, capsys):
    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name)

    def no_network(*args, **kwargs):
        raise AssertionError("replay opened a socket")

    monkeypatch.setattr(socket, "socket", no_network)
    assert cli.main(["replay", str(game)]) == 0
    assert replay.problem(game) is None


def test_replay_by_actions_passes_when_the_prompt_has_changed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = llm_game(tmp_path)
    today = brain.system_prompt
    monkeypatch.setattr(brain, "system_prompt", lambda c: "New words. " + today(c))
    # Today's prompt asks a request no stored reply answers, and replay doesn't care.
    (folder / "replies.jsonl").write_text("")
    assert replay.problem(folder) is None


def test_replay_by_actions_passes_when_the_story_s_wording_has_changed(game):
    changed = records(game)
    for record in changed:
        record["story"] = "Told another way."
        record["result"] = "Said another way."
        record["happened"] = "did it."
        record["saw"]["prompt"] = "An older prompt."
    rewrite(game, changed)
    (game / "story.txt").write_text("An older story.\n")
    assert replay.problem(game) is None


def test_replay_by_actions_fails_naming_the_turn_when_an_outcome_changed(game, capsys):
    changed = records(game)
    x, y = changed[5]["outcome"]["position"]
    changed[5]["outcome"]["position"] = [x, y + 1]
    rewrite(game, changed)
    assert cli.main(["replay", str(game)]) == 1
    error = capsys.readouterr().err
    assert "turn 5" in error
    assert "outcome" in error


@pytest.mark.parametrize("lever,value", [("seed", 6), ("turns_per_pirate", 3)])
def test_replay_by_actions_fails_when_a_rule_changed(game, lever, value):
    path = game / "config.json"
    saved = json.loads(path.read_text())
    saved[lever] = value
    path.write_text(json.dumps(saved))
    assert replay.problem(game) is not None


def test_replay_by_actions_replays_failed_moves_and_passed_turns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Off the island every time: each turn passes after its failed tries.
    folder = llm_game(tmp_path, "not a tool call", WEST)
    played = records(folder)
    assert any(r.get("failed_moves") for r in played)
    assert any(r["did"] == [] for r in played)
    assert replay.problem(folder) is None
    path = folder / "config.json"
    saved = json.loads(path.read_text())
    saved["failed_tries"] = 3  # a rule: how many failed tries a turn allows
    path.write_text(json.dumps(saved))
    assert replay.problem(folder) is not None


def test_replay_by_actions_an_llm_game_needs_no_stored_replies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = llm_game(tmp_path)
    (folder / "replies.jsonl").unlink()
    assert replay.problem(folder) is None


def test_replay_by_actions_leaves_the_folder_untouched(game):
    before = {path.name: path.read_bytes() for path in game.iterdir()}
    assert replay.problem(game) is None
    assert {path.name: path.read_bytes() for path in game.iterdir()} == before


def committed(root, archived=False):
    """Every committed game folder: v2 games, or the first-rules ones if `archived`."""
    return {
        path.parent.relative_to(root).as_posix()
        for top in ("examples", "runs", "results")
        for path in (root / top).rglob("events.jsonl")
        if ("reading_noise" not in (path.parent / "config.json").read_text())
        == archived
    }


def sweep_cells():
    """The incentives sweep's cells, one config and one `runs/` folder each."""
    return {path.stem for path in (ROOT / "configs/incentives").glob("*.toml")}


def test_replay_checks_the_list_holds_the_examples_and_the_committed_runs():
    listed = set(replay.CHECKED)
    assert {
        f"examples/{p.name}" for p in (ROOT / "examples").iterdir() if p.is_dir()
    } <= listed
    runs = {
        game
        for game in committed(ROOT)
        if game.startswith("runs/") and game.split("/")[1] not in sweep_cells()
    }
    assert runs <= listed
    assert listed <= committed(ROOT)  # every listed game is committed
    assert len(listed) < 20  # short, however many games get committed


def test_sweep_games_kept_every_game_the_findings_count_is_committed_and_replays():
    with open(ROOT / "experiments/incentives-findings/games.csv") as games:
        folders = {row["folder"] for row in csv.DictReader(games)}
    assert len(folders) == 130
    assert folders <= committed(ROOT)
    assert not [game for game in folders if replay.problem(ROOT / game)]


def test_sweep_games_kept_one_game_per_cell_is_on_the_replay_list():
    listed = [name.split("/")[1] for name in replay.CHECKED if name.startswith("runs/")]
    assert all(listed.count(cell) == 1 for cell in sweep_cells())
    assert len(sweep_cells()) == 9


@pytest.mark.parametrize("name", replay.CHECKED)
def test_replay_checks_every_listed_game_replays(name):
    assert replay.problem(ROOT / name) is None


def test_replay_checks_replay_all_finds_every_committed_game():
    found = {path.relative_to(ROOT).as_posix() for path in replay.committed_games(ROOT)}
    assert found == committed(ROOT)


def test_first_rules_archived_first_rules_games_leave_the_replay_checks(capsys):
    archived = committed(ROOT, archived=True)
    assert "runs/silent-seed-7" in archived
    assert not archived & set(replay.CHECKED)
    found = {path.relative_to(ROOT).as_posix() for path in replay.archived_games(ROOT)}
    assert found == archived
    # Each stays committed, its folder's README saying it is a first-rules game.
    for game in archived:
        folder = ROOT / game
        readmes = [folder / "README.md", folder.parent / "README.md"]
        assert any(
            "first-rules" in readme.read_text() for readme in readmes if readme.exists()
        ), game


def test_first_rules_archived_replay_all_passes_them_by_saying_how_many(
    tmp_path, capsys
):
    for name in ("examples/silent", "runs/silent-seed-7"):
        shutil.copytree(ROOT / name, tmp_path / name)
    assert replay.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Replayed 1 games" in out
    assert "Passed by 1 first-rules games" in out


def test_first_rules_archived_the_examples_and_reference_games_are_v2():
    for game in [*replay.CHECKED]:
        assert not replay.first_rules(ROOT / game), game


def test_replay_checks_replay_all_names_a_game_that_fails(tmp_path, capsys):
    for name in ("examples/silent", "runs/reference-silent"):
        shutil.copytree(ROOT / name, tmp_path / name)
    broken = records(tmp_path / "runs/reference-silent")
    broken[3]["outcome"]["position"] = [9, 9]
    rewrite(tmp_path / "runs/reference-silent", broken)
    assert replay.main([str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "reference-silent" in out.err
    assert "examples/silent" not in out.err
    assert "2 games" in out.out


def test_replay_by_actions_fails_when_only_the_replay_ends_the_game(game):
    # A game that stopped part way (at a spending cap, say), replayed under a rule
    # that now ends it on its last recorded turn: the ending is a difference too.
    played = records(game)
    del played[-1]["game_over"]
    rewrite(game, played[: len(played)])
    manifest = game / "manifest.json"
    saved = json.loads(manifest.read_text())
    saved["stopped"] = "it reached its spending cap"
    manifest.write_text(json.dumps(saved))
    why = replay.problem(game)
    assert why is not None
    assert "game_over" in why

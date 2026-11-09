# Implements: agents/backend-interface, agents/no-model-yet, agents/free-runs-in-ci, agents/committed-runs
"""What a model backend must do, and the scripted games that spend nothing."""

import json
import os
import socket
from pathlib import Path

import pytest

from treasure_island import cli
from treasure_island.backends import UnknownModel, backend_for
from treasure_island.event_log import log_errors
from treasure_island.runs import Replies

ROOT = Path(__file__).resolve().parents[1]
REQUEST = {"agent": "pirate-1", "model": "some/model", "prompt": "Dig?", "seed": 1}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """No keys, no sockets, and a fresh folder for runs/."""
    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name)

    def no_network(*args, **kwargs):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_backend_interface_a_backend_gives_reply_text_and_its_cost(tmp_path):
    replies = Replies(tmp_path / "replies.jsonl", lambda request: ("dig", 0.25))
    assert replies.ask(REQUEST) == "dig"
    assert replies.cost_usd == 0.25


def test_no_model_yet_every_model_is_refused():
    with pytest.raises(UnknownModel):
        backend_for("some-provider/some-model")


@pytest.mark.parametrize("name", ["silent", "noisy", "loner", "sharer"])
def test_free_runs_in_ci_each_scripted_brain_plays_a_full_game(name, offline, capsys):
    config = ROOT / "configs" / f"{name}.toml"
    assert cli.main(["run", str(config), "--seed", "7", "--no-watch"]) == 0
    folder = offline / capsys.readouterr().out.strip()
    assert json.loads((folder / "manifest.json").read_text())["cost_usd"] == 0
    assert "Game over" in (folder / "story.txt").read_text()


@pytest.mark.parametrize(
    "name",
    [
        "silent/7",
        "reference-silent",
        "v2/0",
        "v2/0-2",
        "none-off/0",
        "competition-off/0-2",
        "deployment-off/0-2",
        "none-reviewed/0-2",
        "competition-reviewed/0-2",
        "deployment-reviewed/0",
        "roomy/0",
        "threshold/0",
        "control/0-2",
    ],
)
def test_committed_runs_pass_the_validator_and_replay(name, offline):
    folder = ROOT / "runs" / name
    assert log_errors(folder / "events.jsonl") == []
    assert cli.main(["replay", str(folder)]) == 0

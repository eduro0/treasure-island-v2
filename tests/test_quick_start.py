# Implements: treasure-island/one-line-replay, treasure-island/python-stack
"""The README's first commands work, exactly as written, with no keys."""

import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from treasure_island import cli

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/eduro0/treasure-island-v2.git"


def readme():
    return (ROOT / "README.md").read_text()


def quick_start():
    """The commands in the README's quick start, in order."""
    section = readme().split("## Quick start", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```bash\n(.*?)```", section, flags=re.DOTALL).group(1)
    return block.strip().splitlines()


def test_one_line_replay_comes_first_in_the_readme():
    headings = re.findall(r"^## (.+)$", readme(), flags=re.MULTILINE)
    assert headings[0] == "Quick start"


def test_one_line_replay_starts_with_a_clone_and_needs_one_more_command():
    clone, enter, show = quick_start()
    assert clone == f"git clone {REPOSITORY}"
    assert enter == "cd treasure-island-v2"
    assert show.startswith("uv run treasure-island show ")


def test_one_line_replay_writes_a_gif_and_a_page_with_no_keys_or_network(
    tmp_path, monkeypatch, capsys
):
    for folder in ("configs", "examples"):
        shutil.copytree(ROOT / folder, tmp_path / folder)
    monkeypatch.chdir(tmp_path)
    for name in os.environ:
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name)

    def no_network(*args, **kwargs):
        raise AssertionError("the quick start opened a socket")

    monkeypatch.setattr(socket, "socket", no_network)
    words = shlex.split(quick_start()[-1])
    assert cli.main(words[3:]) == 0
    printed = capsys.readouterr().out
    gif, page = printed.split()[:2]  # as CI reads them; the transcript comes after
    assert Path(gif).read_bytes().startswith(b"GIF")
    assert "<html" in Path(page).read_text().lower()


def gate_steps():
    flow = yaml.safe_load((ROOT / ".github/workflows/blocking-checks.yml").read_text())
    return flow["jobs"]["blocking-checks"]["steps"]


def clean_clone_step():
    return next(step for step in gate_steps() if step.get("name") == "clean-clone")


def test_one_line_replay_ci_runs_that_exact_command():
    assert quick_start()[-1] in clean_clone_step()["run"]


def test_one_line_replay_ci_runs_it_before_anything_is_installed_with_no_keys():
    steps = gate_steps()
    runs = [str(step.get("run")) for step in steps]
    clean = steps.index(clean_clone_step())
    uv = next(i for i, step in enumerate(steps) if "setup-uv" in step.get("uses", ""))
    assert uv < clean < runs.index("make install")
    assert "env" not in steps[clean]
    assert "secrets." not in steps[clean]["run"]
    # Only the clone runs the command, so it can't be passed by the checkout.
    assert not any(quick_start()[-1] in run for run in runs[:clean] + runs[clean + 1 :])


# Stands in for uv: `show` writes what the test asks for and prints the paths, and
# anything else runs under this Python, which has Pillow, as the clone's would.
FAKE_UV = """#!/bin/sh
if [ "$2" = "treasure-island" ]; then
  [ -e left-behind.txt ] && { echo "the clone holds an uncommitted file" >&2; exit 3; }
  [ "$*" = "run treasure-island show examples/silent" ] || exit 4
  {python} {writer} {mode}
  exit $?
fi
shift 2
exec {python} "$@"
"""
WRITER = """import sys
from PIL import Image
mode = sys.argv[1]
if mode == "fails":
    sys.exit(7)
frames = [Image.new("RGB", (4, 4), colour) for colour in ("red", "green", "blue")]
if mode == "one-frame":
    frames = frames[:1]
frames[0].save("game.gif", save_all=True, append_images=frames[1:])
if mode == "not-a-gif":
    open("game.gif", "w").write("not a gif")
open("game.html", "w").write("nothing here" if mode == "no-page" else "<html></html>")
if mode != "prints-nothing":
    print("game.gif")
    print("game.html")
"""


def run_the_step(tmp_path, mode):
    """Run the step's own script against a small checkout, with a fake uv."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    (checkout / "README.md").write_text("# A checkout\n")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run([*git, "commit", "-qm", "x"], cwd=checkout, check=True)
    (checkout / "left-behind.txt").write_text("from an earlier step\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "writer.py").write_text(WRITER)
    uv = tools / "uv"
    uv.write_text(
        FAKE_UV.replace("{python}", sys.executable)
        .replace("{writer}", str(tools / "writer.py"))
        .replace("{mode}", mode)
    )
    uv.chmod(0o755)
    temp = tmp_path / "temp"
    temp.mkdir()
    env = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(checkout),
        "RUNNER_TEMP": str(temp),
    }
    # GitHub runs a step's script with `bash -e`.
    return subprocess.run(
        ["bash", "-e", "-c", clean_clone_step()["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_one_line_replay_ci_passes_when_a_real_gif_and_page_come_out(tmp_path):
    done = run_the_step(tmp_path, "good")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    "mode", ["fails", "not-a-gif", "one-frame", "no-page", "prints-nothing"]
)
def test_one_line_replay_ci_fails_unless_a_real_gif_and_page_come_out(tmp_path, mode):
    assert run_the_step(tmp_path, mode).returncode != 0


def test_python_stack_the_quick_start_needs_only_uv():
    section = readme().split("## Quick start", 1)[1].split("\n## ", 1)[0]
    assert "uv" in section
    assert "docs.astral.sh/uv" in section

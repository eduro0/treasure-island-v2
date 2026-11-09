"""spec-check: the spec and the code point at each other.

Implements: specs-and-docs/everything-traces, specs-and-docs/every-file-has-a-home, specs-and-docs/areas, specs-and-docs/spec-is-intent, specs-and-docs/short-specs, specs-and-docs/domains, specs-and-docs/prompts-are-verbatim, specs-and-docs/runs-are-evidence, specs-and-docs/art-is-binary

The spec lives in product/spec/, experiments/spec/ and contributing/spec/, one
file per area. Each
file lists named statements (`- **name:** sentence`) and ends with a
"## Where this is enforced" table: statement | enforced by | tested by. Every
source file names the statements it implements on an `Implements:` line,
except prompt files (prompts/): models read them verbatim, so a table row
homes them and they never carry an `Implements:` line. Committed run folders
(runs/, examples/ and archive/) are the same: output the game wrote, homed by a row such as
`runs/survey/**`. JSON Lines files never hold a comment, wherever they are,
and binary images (PNG, GIF, ...) are never asked for one or for a home.

This script fails (exit 1, GitHub errors) on anything broken, and only warns
about statements marked "not yet" (planned work) and long spec files:
- malformed spec files, duplicate names, areas used in two domains;
- statements without a table row, rows for unknown statements, statements
  marked "not yet";
- files a row names that don't exist or don't name the statement back;
- `Implements:` lines naming unknown statements, or inside a prompt file or a
  committed run;
- tests a row lists that don't exist;
- source files with no home (no `Implements:` line and no table row);
- spec files over 300 lines.

Runs on any Python 3 (`make spec-check` uses uv's Python; pr_gate imports `areas`).
"""

import ast
import os
import re
import subprocess
import sys
from functools import cache
from pathlib import Path

DOMAINS = ("product", "experiments", "contributing")
PROMPTS = "prompts/"
RUNS = ("runs/", "examples/", "results/", "archive/")  # output the game wrote
MAX_LINES = 300
TABLE_HEADING = "## Where this is enforced"
_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_STATEMENT = re.compile(rf"^- \*\*({_NAME}):\*\* \S")
_REF = re.compile(rf"\b({_NAME}/{_NAME})\b")
_ROW = re.compile(rf"^\|\s*`({_NAME})`\s*\|([^|]*)\|([^|]*)\|\s*$")
_TICKED = re.compile(r"`([^`]+)`")
_IMPLEMENTS = re.compile(
    r"^\s*(?:#|//|/\*|<!--|\"\"\"|--)?\s*Implements:\s*(.*)", re.MULTILINE
)
_JS_TEST = re.compile(
    r"\b(?:it|test)(?:\.(?:each|only|concurrent))?(?:\([^)]*\))?\(\s*['\"`]([^'\"`]+)"
)
_PLACEHOLDER = re.compile(r"[%$][a-z#]+")
_SKIPPED_DIRS = {".git", ".venv", "node_modules", "dist", "staticfiles"}
_EXEMPT_NAMES = {"uv.lock", "package-lock.json", ".gitignore"}
_EXEMPT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}
_NO_COMMENTS = {
    ".json",
    ".jsonl",
    ".csv",
    ".gz",
    ".docx",
    ".pdf",
    ".gif",
}  # data, archives, documents, images
_NO_COMMENT_NAMES = {".python-version", "uv.lock", "package-lock.json", "LICENSE"}


@cache
def _glob(pattern):
    """`**` matches across folders, `*` within one; works on any Python 3."""
    parts = re.split(r"(\*\*/?|\*)", pattern)
    regex = "".join(
        ".*" if part.startswith("**") else "[^/]*" if part == "*" else re.escape(part)
        for part in parts
    )
    return re.compile(f"^{regex}$")


def _basename(path):
    return path.rsplit("/", 1)[-1]


def _spec_files(root, domain):
    folder = Path(root, domain, "spec")
    return sorted(p for p in folder.glob("*.md") if p.name != "README.md")


def areas(root):
    """The area registry: spec file names per domain."""
    return {d: {p.stem for p in _spec_files(root, d)} for d in DOMAINS}


def _tracked_files(root):
    """Files git tracks under `root`, or every file when it isn't a repository."""
    try:
        listed = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        if listed:
            return listed
    except (OSError, subprocess.CalledProcessError):
        pass
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIPPED_DIRS]
        found += [Path(folder, f).relative_to(root).as_posix() for f in files]
    return found


def _parse_spec(path, rel, warnings):
    """Return ({statement: line}, [(statement, enforced_by, tested_by)])."""
    lines = path.read_text().splitlines()
    if not next((line for line in lines if line.strip()), "").startswith("# "):
        warnings.append(f"{rel}: must start with '# <Area title>'.")
    statements, rows, in_table = {}, [], False
    for number, line in enumerate(lines, start=1):
        if line.strip() == TABLE_HEADING:
            in_table = True
        elif in_table:
            match = _ROW.match(line)
            if match:
                rows.append(match.groups())
        elif match := _STATEMENT.match(line):
            if match.group(1) in statements:
                warnings.append(
                    f"{rel}:{number}: statement name '{match.group(1)}' is used twice."
                )
            statements.setdefault(match.group(1), number)
        elif line.startswith("- "):
            warnings.append(
                f"{rel}:{number}: statement must be '- **name:** sentence'."
            )
    if statements and not in_table:
        warnings.append(f"{rel}: has no '{TABLE_HEADING}' table.")
    return statements, rows


def _implements(root, path):
    """Statements a file names on its `Implements:` lines."""
    full = Path(root, path)
    if full.is_symlink() or not full.is_file():
        return set()
    refs = set()
    for match in _IMPLEMENTS.finditer(full.read_text(errors="ignore")):
        refs.update(_REF.findall(match.group(1)))
    return refs


def _test_names(root, tracked):
    names = []
    for path in tracked:
        name = _basename(path)
        if not (name.startswith("test") or ".test." in name or ".spec." in name):
            continue
        full = Path(root, path)
        if not full.is_file() or full.is_symlink():
            continue
        text = full.read_text(errors="ignore")
        if path.endswith(".py"):
            names += _python_tests(text)
        else:
            names += [t.lower().replace(" ", "_") for t in _JS_TEST.findall(text)]
    return names


def _python_tests(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    ]


def _test_key(name):
    """A test's name for matching: whole, case-blind, with spaces as underscores
    and parameter placeholders (`%s`, `$value`) dropped, so a listed test only
    matches the test of exactly that name."""
    name = _PLACEHOLDER.sub("", name.lower()).replace(" ", "_")
    return re.sub(r"_+", "_", name).strip("_")


def _needs_a_home(root, path):
    full = Path(root, path)
    return not (
        any(path.startswith(f"{d}/spec/") for d in DOMAINS)
        or _basename(path) in _EXEMPT_NAMES
        or Path(path).suffix in _EXEMPT_SUFFIXES
        or (full.is_file() and not full.is_symlink() and full.stat().st_size == 0)
    )


def _can_hold_comments(root, path):
    return not (
        Path(root, path).is_symlink()
        or Path(path).suffix in _NO_COMMENTS
        or _basename(path) in _NO_COMMENT_NAMES
        or path.startswith((PROMPTS, *RUNS))
    )


def _check_layout(root, warnings):
    for domain in DOMAINS:
        folder = Path(root, domain, "spec")
        if not folder.is_dir():
            warnings.append(f"Missing folder {domain}/spec.")
        elif not (folder / "README.md").is_file():
            warnings.append(f"Missing {domain}/spec/README.md.")
    registry = areas(root)
    for i, first in enumerate(DOMAINS):
        for second in DOMAINS[i + 1 :]:
            for area in sorted(registry[first] & registry[second]):
                warnings.append(
                    f"Area '{area}' is defined in both {first} and {second}; "
                    "area names must be unique."
                )


class _Repo:
    """What the checks share: files, their Implements lines, tests, statements."""

    def __init__(self, root, warnings):
        self.root = root
        self.tracked = _tracked_files(root)
        self.implements = {f: _implements(root, f) for f in self.tracked}
        self.tests = _test_names(root, self.tracked)
        self.statements, self.rows = {}, []
        for domain in DOMAINS:
            for path in _spec_files(root, domain):
                rel = path.relative_to(root).as_posix()
                names, rows = _parse_spec(path, rel, warnings)
                self.statements.update({f"{path.stem}/{n}": rel for n in names})
                self.rows += [(rel, path.stem, *row) for row in rows]


def _check_row(repo, row, warnings):
    """Check one table row; return the files it names."""
    rel, area, name, enforced, tested = row
    ref = f"{area}/{name}"
    if ref not in repo.statements:
        warnings.append(f"{rel}: table row '{name}' names no statement.")
        return set()
    if not enforced.strip() or enforced.strip() in ("—", "-"):
        warnings.append(
            f"{rel}: '{name}' names nothing that enforces it; name files, "
            "reviewer or a setting, or 'not yet'."
        )
    if "not yet" in enforced.lower():
        warnings.append(f"{rel}: '{name}' is not enforced yet.")
    named = set()
    for pattern in _TICKED.findall(enforced):
        hits = {f for f in repo.tracked if _glob(pattern).match(f)}
        if not hits:
            warnings.append(
                f"{rel}: '{name}' names `{pattern}`, which matches no file."
            )
        for path in sorted(hits):
            if _can_hold_comments(repo.root, path) and ref not in repo.implements[path]:
                warnings.append(
                    f"{path} enforces '{ref}' but its Implements: line doesn't name it."
                )
        named |= hits
    known = {_test_key(t) for t in repo.tests}
    for test in _TICKED.findall(tested):
        if _test_key(test) not in known:
            warnings.append(
                f"{rel}: '{name}' lists test '{test}', which doesn't exist."
            )
    return named


def _verbatim(path):
    """What a file is if it must stay exactly as written, else None."""
    if path.startswith(PROMPTS):
        return "a prompt the model reads verbatim"
    if path.startswith(RUNS):
        return "run output the game wrote"
    return None


def _check_traces(repo, warnings):
    homed = set()
    rowed = set()
    for row in repo.rows:
        homed |= _check_row(repo, row, warnings)
        rowed.add(f"{row[1]}/{row[2]}")
    for ref, rel in sorted(repo.statements.items()):
        if ref not in rowed:
            warnings.append(
                f"{rel}: statement '{ref.split('/')[1]}' has no row in "
                "'Where this is enforced'."
            )
    for path, refs in sorted(repo.implements.items()):
        for ref in sorted(refs - repo.statements.keys()):
            warnings.append(f"{path} implements unknown statement '{ref}'.")
        if refs and (kind := _verbatim(path)):
            warnings.append(
                f"{path} is {kind}: home it with a spec table row, not an "
                "Implements: line."
            )
        if refs:
            homed.add(path)
    for path in sorted(repo.tracked):
        if path not in homed and _needs_a_home(repo.root, path):
            warnings.append(
                f"{path} has no home: add an 'Implements:' line or list it in a "
                "spec table."
            )


def _check_lengths(root, warnings):
    for domain in DOMAINS:
        for path in _spec_files(root, domain):
            lines = len(path.read_text().splitlines())
            if lines > MAX_LINES:
                warnings.append(
                    f"{path.relative_to(root)} is {lines} lines; consider splitting "
                    f"this area (limit {MAX_LINES})."
                )


def check(root):
    """Return every warning for the repository at `root`."""
    warnings = []
    _check_layout(root, warnings)
    _check_traces(_Repo(root, warnings), warnings)
    _check_lengths(root, warnings)
    return warnings


_ADVISORY = ("is not enforced yet.", "consider splitting this area")


def blocking(warnings):
    """The findings that fail CI: everything except planned work and long specs."""
    return [w for w in warnings if not any(a in w for a in _ADVISORY)]


def main():
    warnings = check(Path.cwd())
    errors = blocking(warnings)
    for warning in warnings:
        level = "error" if warning in errors else "warning"
        print(f"::{level} title=spec-check::{warning}")
    print(
        f"spec-check: {len(errors)} error(s), {len(warnings) - len(errors)} warning(s)."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

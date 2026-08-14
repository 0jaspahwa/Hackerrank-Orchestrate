"""Pre-submission checks. Exit 0 only if every one passes.

These check the things that make a submission unscoreable rather than merely
wrong: a malformed file, a nondeterministic pipeline, an invented evidence id,
or a module that only imports because this machine happens to have a key set.

  py code/preflight.py            run all checks
  py code/preflight.py --emit P   internal: route all 110 and write to P
"""

import csv
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import build_context, route_ctx  # noqa: E402
from schema import COLUMNS, Action, MessageType  # noqa: E402
from writer import write_output  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "dataset"
OUTPUT = DATASET / "output.csv"
MODULES = ["schema", "context", "observe", "media", "branches", "writer", "main", "run_all"]

results: list[tuple[str, bool, str]] = []


def check(label: str, passed: bool, detail: str = "") -> bool:
    results.append((label, passed, detail))
    return passed


def emit(path: Path) -> None:
    """Route all 110 and write them. Used by the determinism check."""
    with open(DATASET / "messages.csv", encoding="utf-8") as fh:
        ids = [r["message_id"] for r in csv.DictReader(fh)]
    write_output([route_ctx(build_context(mid)) for mid in ids], path)


def a_file_shape() -> None:
    if not check("a1  output.csv exists", OUTPUT.exists()):
        return
    raw = OUTPUT.read_bytes()
    lines = raw.splitlines()
    check("a2  111 lines (header + 110)", len(lines) == 111, f"{len(lines)} lines")
    expected = ",".join(COLUMNS).encode("utf-8")
    check("a3  header byte-identical to COLUMNS", lines[0] == expected, lines[0].decode())


def b_determinism() -> None:
    tmp = [REPO / "dataset" / f"_preflight_run{i}.csv" for i in (1, 2)]
    try:
        for path in tmp:
            subprocess.run([sys.executable, str(Path(__file__)), "--emit", str(path)],
                           check=True, capture_output=True, cwd=REPO)
        one, two = (p.read_bytes() for p in tmp)
        if one == two:
            check("b   two fresh runs byte-identical", True, f"{len(one)} bytes each")
            return
        diffs = [i for i, (x, y) in enumerate(zip(one.splitlines(), two.splitlines())) if x != y]
        check("b   two fresh runs byte-identical", False, f"{len(diffs)} differing lines: {diffs[:5]}")
    except subprocess.CalledProcessError as exc:
        check("b   two fresh runs byte-identical", False, exc.stderr.decode()[-200:])
    finally:
        for path in tmp:
            path.unlink(missing_ok=True)


def c_cells_and_enums() -> None:
    with open(OUTPUT, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    empty = [r["message_id"] for r in rows if not all(str(v).strip() for v in r.values())]
    check("c1  no empty cells", not empty, f"{len(empty)} bad: {empty[:5]}")
    actions = {a.value for a in Action}
    types = {t.value for t in MessageType}
    bad_a = [r["message_id"] for r in rows if r["action"] not in actions]
    bad_t = [r["message_id"] for r in rows if r["message_type"] not in types]
    check("c2  every action is a valid enum", not bad_a, f"{bad_a[:5]}")
    check("c3  every message_type is a valid enum", not bad_t, f"{bad_t[:5]}")
    bad_c = [r["message_id"] for r in rows if not 0.0 <= float(r["confidence"]) <= 1.0]
    check("c4  every confidence in [0,1]", not bad_c, f"{bad_c[:5]}")


def d_evidence_resolves() -> None:
    with open(DATASET / "message_history.csv", encoding="utf-8") as fh:
        known = {r["message_id"] for r in csv.DictReader(fh)}
    with open(OUTPUT, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    dangling = []
    for row in rows:
        cell = row["evidence_message_ids"]
        if cell == "none":
            continue
        for eid in cell.split(";"):
            if eid not in known:
                dangling.append((row["message_id"], eid))
    check("d   every evidence id is 'none' or real", not dangling, f"{len(dangling)} dangling: {dangling[:3]}")


def e_fresh_clone_imports() -> None:
    """Import every module with no credentials in the environment."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")}
    env["ANTHROPIC_API_KEY"] = ""
    failed = []
    for module in MODULES:
        proc = subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{Path(__file__).parent}'); import {module}"],
            capture_output=True, env=env, cwd=REPO,
        )
        if proc.returncode != 0:
            failed.append(f"{module}: {proc.stderr.decode().strip().splitlines()[-1][:80]}")
    check("e   all modules import with no credentials", not failed, "; ".join(failed))


def f_requirements_declared() -> None:
    """Every pinned dependency is installed and importable.

    Check (e) proves the modules import on this machine; it does not prove the
    dependencies were declared. This closes that gap from the other side.
    """
    req = REPO / "requirements.txt"
    if not check("f1  requirements.txt exists", req.exists()):
        return
    # Distribution name -> import name, where they differ.
    import_name = {"faster-whisper": "faster_whisper", "python-dotenv": "dotenv"}
    listed, missing = [], []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        dist = re.split(r"[=<>!~\[]", line)[0].strip()
        listed.append(dist)
        proc = subprocess.run(
            [sys.executable, "-c", f"import {import_name.get(dist, dist)}"],
            capture_output=True, cwd=REPO,
        )
        if proc.returncode != 0:
            missing.append(dist)
    check("f2  every listed package is importable", not missing,
          f"missing: {missing}" if missing else "")
    check("f3  requirements.txt is non-empty", bool(listed), f"{len(listed)} packages")


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--emit":
        emit(Path(sys.argv[2]))
        return 0

    a_file_shape()
    b_determinism()
    c_cells_and_enums()
    d_evidence_resolves()
    e_fresh_clone_imports()
    f_requirements_declared()

    print("PREFLIGHT")
    print("-" * 62)
    for label, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {label:42} {detail if not passed else ''}")
    ok = all(p for _, p, _ in results)
    print("-" * 62)
    print(f"  {sum(p for _, p, _ in results)}/{len(results)} checks passed -> exit {0 if ok else 1}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

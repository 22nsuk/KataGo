#!/usr/bin/env python3
"""Exercise Fox exclusion through writetrainingdata, including NPZ output and accounting.

Use an Eigen build and the repository's small test model. Fixtures are synthetic
30-move records, not evidence that additional real-world Fox handicaps are safe.
"""

import argparse
import ast
import json
from pathlib import Path
import re
import struct
import subprocess
import zipfile


MOVE_COUNT = 30


def sgf(handicap: str | None, komi: str, rules: str, *, app: bool = True,
        placements: str = "") -> str:
    # Two separated groups grow down opposite sides; no passes or captures.
    points = [(column, row) for column in range(2) for row in range(9)][:15]
    moves = "".join(
        f";B[{chr(97 + x)}{chr(97 + y)}];W[{chr(105 - x)}{chr(97 + y)}]"
        for x, y in points
    )
    return (
        "(;GM[1]FF[4]SZ[9]PB[filter-test-black]PW[filter-test-white]"
        "BR[1d]WR[1d]DT[2024-01-01]TM[600]RE[?]"
        + ("AP[foxwq]" if app else "")
        + (f"HA[{handicap}]" if handicap is not None else "")
        + f"KM[{komi}]RU[{rules}]" + placements + moves + ")\n"
    )


def npz_rows(path: Path) -> int:
    # Read the row dimension without adding numpy as a dependency of this test.
    with zipfile.ZipFile(path) as archive:
        with archive.open("globalInputNC.npy") as data:
            assert data.read(6) == b"\x93NUMPY", path
            version = tuple(data.read(2))
            assert version in ((1, 0), (2, 0)), (path, version)
            size_format = "<H" if version == (1, 0) else "<I"
            length = struct.unpack(size_format, data.read(struct.calcsize(size_format)))[0]
            header = ast.literal_eval(data.read(length).decode("latin1"))
            shape = header["shape"]
            assert len(shape) == 2 and shape[0] > 0, (path, shape)
            return shape[0]


def done_counts(log: str) -> dict[str, int]:
    counts = {}
    reading = False
    for line in log.splitlines():
        if line.endswith("Counts by done"):
            reading = True
        elif reading:
            if "==========" in line:
                return counts
            match = re.search(r"\b([A-Za-z][A-Za-z0-9]+): ([0-9]+)$", line)
            assert match is not None, f"Unexpected done-count line: {line}"
            assert match[1] not in counts, line
            counts[match[1]] = int(match[2])
    raise AssertionError("Missing complete Counts by done section")


def run_case(engine: Path, model: Path, root: Path, name: str, source: str,
             records: list[str], expected: dict[str, int], expected_rows: int) -> dict:
    directory = root / name
    inputs = directory / "sgfs"
    inputs.mkdir(parents=True)
    for index, record in enumerate(records):
        (inputs / f"{index:02d}.sgf").write_text(record, encoding="utf-8")
    empty = directory / "empty-users.txt"
    empty.write_text("", encoding="utf-8")
    config = directory / "test.cfg"
    config.write_text(
        "dataBoardLen = 9\nallowedBoardSizes = 9-9\n"
        "maxApproxRowsPerTrainFile = 32\n"
        "nnMaxBatchSize = 8\nnumEigenThreads = 1\n"
        "nnCacheSizePowerOfTwo = 10\n",
        encoding="utf-8",
    )
    output = directory / "data"
    command = [
        str(engine), "writetrainingdata", "-model", str(model), "-config", str(config),
        "-sgfdir", str(inputs), "-what-data-source", source, "-output-dir", str(output),
        "-no-train-users-file", str(empty), "-no-game-users-file", str(empty),
        "-is-bot-users-file", str(empty), "-keep-prob", "1", "-max-visits", "2",
    ]
    (directory / "command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    with (directory / "process.log").open("w", encoding="utf-8") as stream:
        subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=120)
    log = (output / "log.log").read_text(encoding="utf-8")
    counts = done_counts(log)
    totals = re.findall(r"\bTotal rows: ([0-9]+)", log)
    assert len(totals) == 1, (name, totals)
    archives = sorted(output.glob("*.npz"))
    rows = sum(npz_rows(path) for path in archives)
    observed = {"case": name, "records": len(records), "done": counts,
                "logged_rows": int(totals[0]), "npz_rows": rows, "npz_files": len(archives)}
    (directory / "observed.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")
    assert counts == expected, f"{name}: completion counts {counts} != {expected}"
    assert sum(counts.values()) == len(records), (name, counts)
    assert int(totals[0]) == rows == expected_rows, (name, observed, expected_rows)
    assert not list(output.glob("*.tmp")), f"{name}: incomplete output archive"
    if expected_rows == 0:
        assert not archives, f"{name}: excluded records produced NPZ output"
    print(json.dumps(observed), flush=True)
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; must not exist")
    parser.add_argument("--case", choices=("rejected", "accepted", "mixed", "non-fox"), action="append")
    args = parser.parse_args()
    engine, model, root = args.engine.resolve(strict=True), args.model.resolve(strict=True), args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)

    rejected = [sgf("1", komi, rules) for rules, komi in (
        ("Japanese", "6.5"), ("Chinese", "7.5"), ("Japanese", "325"),
        ("Chinese", "750"), ("Japanese", "275"), ("Chinese", "700"),
    )]
    accepted = [sgf(handicap, komi, rules) for rules, handicap, komi in (
        ("Japanese", "0", "0"), ("Japanese", "1", "0"),
        ("Japanese", "0", "6.5"), ("Japanese", "0", "7.5"),
        ("Chinese", "1", "0"), ("Chinese", "0", "7.5"),
    )] + [sgf("0", "325", "Japanese", app=False), sgf("0", "750", "Chinese", app=False)]
    # Preserve the separate handicap exclusion policy (including missing HA).
    skipped = [sgf("2", "0", "Chinese", placements="AB[cc][gg]"),
               sgf("2", "0", "Chinese"), sgf(None, "7.5", "Chinese")]
    cases = [
        ("rejected", "fox", rejected, {"GameHandicap1MismatchKomi": len(rejected)}, 0),
        ("accepted", "fox", accepted, {"Used": len(accepted)}, MOVE_COUNT * len(accepted)),
        ("mixed", "fox", rejected + accepted + skipped,
         {"GameHandicap1MismatchKomi": len(rejected), "Used": len(accepted),
          "GameSkipHandicap": len(skipped)}, MOVE_COUNT * len(accepted)),
        ("non-fox", "ogs", [sgf("1", "6.5", "Japanese", app=False),
                              sgf("1", "7.5", "Chinese", app=False)], {"Used": 2}, 2 * MOVE_COUNT),
    ]
    results = [run_case(engine, model, root, *case) for case in cases
               if args.case is None or case[0] in args.case]
    (root / "result.json").write_text(
        json.dumps({"status": "PASS", "cases": results}, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

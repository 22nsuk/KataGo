#!/usr/bin/env python3
"""Check SGF winners in actual training targets using an Eigen engine and test model."""

import argparse
import json
from pathlib import Path
import struct
import zipfile

from test_fox_sgf_filter import MOVE_COUNT, npy_header, run_case, sgf


def check_winner_targets(directory: Path, winner: str) -> int:
    turns = []
    for path in sorted(directory.glob('*.npz')):
        with zipfile.ZipFile(path) as archive, archive.open('globalTargetsNC') as data:
            header = npy_header(data)
            shape = header['shape']
            assert len(shape) == 2 and shape[0] > 0 and shape[1] == 80, (path, header)
            assert header['descr'] == '<f4' and not header['fortran_order'], (path, header)
            payload = data.read()
            assert len(payload) == shape[0] * 80 * 4, (path, len(payload), shape)
            for row in struct.iter_unpack('<80f', payload):
                # Channel 51 records the turn. The fixture alternates from Black,
                # while channels 0:3 encode win/loss/no-result for the player to move.
                turn = int(row[51])
                assert row[51] == turn and 0 <= turn < MOVE_COUNT, (path, row[51])
                player = 'B' if turn % 2 == 0 else 'W'
                expected = (1.0, 0.0, 0.0) if player == winner else (0.0, 1.0, 0.0)
                assert row[:3] == expected, (path, turn, player, winner, row[:3], expected)
                # Incomplete records still train the result with positive weight,
                # but must not claim a known final ownership map.
                assert 0.0 <= row[35] < 1.0, (path, turn, 'value weight', 1.0 - row[35])
                assert row[27] == 0.0, (path, turn, 'ownership weight', row[27])
                turns.append(turn)
    assert sorted(turns) == list(range(MOVE_COUNT)), (directory, turns)
    return len(turns)


def run(engine: Path, model: Path, root: Path):
    root.mkdir(parents=True, exist_ok=False)
    cases = (
        # name, source, RE value, winner
        ('fox-black', 'fox', 'B+', 'B'),
        ('fox-white', 'fox', 'W+', 'W'),
        ('ogs-black', 'ogs', 'B+', 'B'),
        ('ogs-white', 'ogs', 'W+', 'W'),
        ('black-normalized', 'fox', ' b+ ', 'B'),
        ('white-normalized', 'fox', ' w+ ', 'W'),
        ('black-incomplete', 'fox', 'B+ further moves not recorded', 'B'),
        ('white-incomplete', 'fox', 'W+ further moves not recorded', 'W'),
    )
    results = []
    for name, source, result, winner in cases:
        record = sgf('0', '6.5', 'Japanese', app=source == 'fox', result=result)
        observed = run_case(engine, model, root, name, source, [record], {'Used': 1}, MOVE_COUNT, workers=1)
        checked = check_winner_targets(root / name / 'data', winner)
        results.append({**observed, 'result': result, 'winner': winner, 'checkedTargetRows': checked})

    (root / 'result.json').write_text(json.dumps(
        {'status': 'PASS', 'cases': results}, indent=2) + '\n', encoding='utf-8')
    print(f'Training data results: PASS ({len(cases)} cases, {sum(c["checkedTargetRows"] for c in results)} target rows)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; must not exist')
    args = parser.parse_args()
    run(args.engine.resolve(strict=True), args.model.resolve(strict=True), args.output.resolve())

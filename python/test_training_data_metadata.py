#!/usr/bin/env python3
"""Check per-SGF metadata error isolation and buffered NPZ preservation with an Eigen engine."""

import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import subprocess
import zipfile

from test_fox_sgf_filter import MOVE_COUNT, npy_header, run_case, sgf


def record(source: str, result: str = 'B+', **properties: str) -> str:
    text = sgf('0', '6.5', 'Japanese', app=source == 'fox', result=result)
    for key, value in properties.items():
        if key == 'DT':
            text = text.replace('DT[2024-01-01]', f'DT[{value}]')
        elif key == 'TM':
            text = text.replace('TM[600]', f'TM[{value}]')
        else:
            text = text.replace(';B[', f'{key}[{value}];B[', 1)
    return text


def check_preserved_games(directory: Path):
    observed = Counter()
    for path in directory.glob('*.npz'):
        with zipfile.ZipFile(path) as archive, archive.open('globalTargetsNC') as data:
            header = npy_header(data)
            rows, channels = header['shape']
            assert channels == 80 and header['descr'] == '<f4' and not header['fortran_order'], header
            payload = data.read()
            assert len(payload) == rows * channels * 4, (path, len(payload), header)
            for row in struct.iter_unpack('<80f', payload):
                turn = int(row[51])
                assert row[51] == turn and 0 <= turn < MOVE_COUNT, (path, row[51])
                assert row[:3] in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), (path, row[:3])
                assert 0.0 <= row[35] < 1.0, (path, 'result weight', row[35])
                player = 'B' if turn % 2 == 0 else 'W'
                winner = player if row[0] == 1 else ('W' if player == 'B' else 'B')
                observed[winner, turn] += 1
    # Both the buffered Black win and the later White win must survive exactly once.
    expected = Counter({(winner, turn): 1 for winner in ('B', 'W') for turn in range(MOVE_COUNT)})
    assert observed == expected, (directory, observed, expected)


def run(engine: Path, model: Path, root: Path):
    root.mkdir(parents=True, exist_ok=False)
    cases = []
    for source in ('fox', 'ogs', 'kgs'):
        cases.extend((
            (f'{source}-tm-text', source, {'TM': 'invalid'}, 'BadTimeControl'),
            (f'{source}-tm-range', source, {'TM': '-1'}, 'BadTimeControl'),
            (f'{source}-date-text', source, {'DT': 'not-a-date'}, 'GameBadDate'),
            (f'{source}-date-range', source, {'DT': '2024-02-30'}, 'GameBadDate'),
        ))
    cases.extend((
        ('fox-period-count', 'fox', {'TC': 'invalid', 'TT': '30'}, 'BadTimeControl'),
        ('fox-period-time', 'fox', {'TC': '5', 'TT': 'invalid'}, 'BadTimeControl'),
        ('fox-missing-time', 'fox', {'TM': ''}, 'BadTimeControl'),
        ('go4go-date-text', 'go4go', {'DT': 'not-a-date'}, 'GameBadDate'),
        ('go4go-date-range', 'go4go', {'DT': '2024-02-30'}, 'GameBadDate'),
        ('gogod-date-range', 'gogod', {'DT': '2024-02-30'}, 'GameBadDate'),
        ('gogod-date-text', 'gogod', {'DT': 'not-a-date'}, 'GameDateParsing'),
    ))
    for source in ('ogs', 'kgs'):
        for name, ot in (('byoyomi', '30 byo-yomi'), ('canadian', '25 canadian'),
                         ('fischer', 'invalid fischer'), ('simple', 'invalid simple')):
            cases.append((f'{source}-{name}', source, {'OT': ot}, 'BadTimeControl'))
        cases.append((f'{source}-ot-only', source, {'TM': '', 'OT': '5x30 byo-yomi'}, 'BadTimeControl'))

    results = []
    for name, source, properties, reason in cases:
        observed = run_case(
            engine, model, root, name, source,
            [record(source, 'B+'), record(source, **properties), record(source, 'W+')],
            {'Used': 2, reason: 1}, 2 * MOVE_COUNT,
            workers=1, ordered_files=True, max_rows=128)
        check_preserved_games(root / name / 'data')
        log = (root / name / 'data' / 'log.log').read_text(encoding='utf-8')
        assert '01.sgf' in log and 'All done' in log, (name, log)
        # Fewer than 128 rows: all surviving rows are flushed only after the batch.
        assert observed['npz_files'] == 1, observed
        results.append(observed)

    # Valid formats still work, including GoGoD's historical date normalization.
    valid = {
        'fox': [{}, {'TC': '5', 'TT': '30'}],
        'ogs': [{}, {'TM': ''}, {'OT': '5x30 byo-yomi'}, {'OT': '25/600 Canadian'},
                {'OT': '10 fischer'}, {'OT': '30 simple'}],
        'kgs': [{}, {'OT': '5x30 byo-yomi'}, {'OT': '25/600 canadian'}],
        'go4go': [{}],
        'gogod': [{}, {'DT': '2024'}, {'DT': '2024-02'}, {'DT': '2024-00-00'}],
    }
    for source, formats in valid.items():
        results.append(run_case(
            engine, model, root, f'{source}-valid', source,
            [record(source, **properties) for properties in formats],
            {'Used': len(formats)}, MOVE_COUNT * len(formats), workers=1, ordered_files=True))

    invalid = [record('fox', TM='invalid'), record('fox', DT='not-a-date')]
    results.append(run_case(engine, model, root, 'all-invalid', 'fox', invalid,
                            {'BadTimeControl': 1, 'GameBadDate': 1}, 0, workers=2))
    results.append(run_case(
        engine, model, root, 'parallel-mixed', 'fox',
        [record('fox', 'B+'), *invalid, record('fox', 'W+')],
        {'Used': 2, 'BadTimeControl': 1, 'GameBadDate': 1}, 2 * MOVE_COUNT, workers=2, max_rows=128))
    check_preserved_games(root / 'parallel-mixed' / 'data')

    # Fatal model setup errors must not become successful per-record skips.
    try:
        run_case(engine, root / 'missing-model.bin.gz', root, 'fatal-model', 'fox',
                 [record('fox')], {}, 0, workers=1)
    except subprocess.CalledProcessError as error:
        assert error.returncode != 0
        log = (root / 'fatal-model' / 'process.log').read_text(encoding='utf-8')
        assert 'missing-model.bin.gz' in log and 'All done' not in log, log
        assert not list((root / 'fatal-model' / 'data').glob('*.npz'))
    else:
        raise AssertionError('Missing model was silently accepted')

    (root / 'result.json').write_text(json.dumps(
        {'status': 'PASS', 'cases': results, 'fatalModelRejected': True}, indent=2) + '\n', encoding='utf-8')
    print(f'Training metadata: PASS ({len(cases)} isolated errors, {len(valid)} valid-format batches, empty/parallel/fatal checks)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; must not exist')
    args = parser.parse_args()
    run(args.engine.resolve(strict=True), args.model.resolve(strict=True), args.output.resolve())

#!/usr/bin/env python3
"""Check worker limits using an Eigen engine, real SGF conversion, and NPZ output."""

import argparse
import json
from pathlib import Path
import re
import subprocess

from test_fox_sgf_filter import MOVE_COUNT, run_case, sgf


def run(engine: Path, model: Path, root: Path):
    root.mkdir(parents=True, exist_ok=False)
    record = sgf('0', '6.5', 'Japanese')
    # Expected counts are explicit so all three bounds are exercised independently.
    cases = (
        # name, input files, configured workers, file limit, processed files, workers
        ('default-single', 1, None, None, 1, 1),
        ('one-worker', 3, 1, None, 3, 1),
        ('two-workers', 4, 2, None, 4, 2),
        ('more-workers-than-files', 2, 8, None, 2, 2),
        ('maximum-worker-setting', 1, 1024, None, 1, 1),
        ('file-limit', 4, 8, 1, 1, 1),
        ('empty-input', 0, None, None, 0, 0),
        ('zero-file-limit', 3, 2, 0, 0, 0),
    )
    results = []
    for name, count, workers, limit, processed, active in cases:
        # No work must succeed without even opening a model, not merely discard output.
        case_model = model if processed else root / 'must-not-open-model.bin.gz'
        observed = run_case(
            engine, case_model, root, name, 'fox', [record] * count,
            {'Used': processed} if processed else {}, MOVE_COUNT * processed,
            workers=workers, max_files=limit)
        log = (root / name / 'data' / 'log.log').read_text(encoding='utf-8')
        # With Eigen's default server-thread selection, actual NN concurrency must
        # not exceed the effective file-worker count (even if nnMaxBatchSize is set).
        nn_threads = {int(i) for i in re.findall(r'Eigen \(CPU\) backend thread ([0-9]+):', log)}
        if processed:
            assert nn_threads and nn_threads <= set(range(active)), (name, active, nn_threads)
        else:
            assert not nn_threads, (name, nn_threads)
        configured = 64 if workers is None else workers
        assert f'Processing {processed} files with {active} worker threads (configured limit {configured})' in log, log
        initialized = re.findall(r'Initialized ([0-9]+) training workers', log)
        assert initialized == ([str(active)] if processed else []), (name, initialized)
        if processed:
            assert 'Loaded neural net' in log
        else:
            assert 'Loaded neural net' not in log
            assert 'skipping neural net and worker initialization' in log
        if limit is not None and processed:
            assert f'Done {processed} / {processed} sgfs' in log
        assert 'All done' in log
        results.append({**observed, 'workers': active})

    for invalid in (0, -1, 1025, 'not-an-integer'):
        name = f'invalid-{invalid}'
        try:
            run_case(engine, model, root, name, 'fox', [], {}, 0, workers=invalid)
        except subprocess.CalledProcessError as error:
            assert error.returncode == 1, (name, error.returncode)
            log = (root / name / 'process.log').read_text(encoding='utf-8')
            assert 'numWorkerThreads' in log, log
            assert 'Loaded neural net' not in log, log
            assert not list((root / name / 'data').glob('*.npz'))
        else:
            raise AssertionError(f'Invalid worker setting accepted: {invalid}')

    (root / 'result.json').write_text(json.dumps(
        {'status': 'PASS', 'cases': results, 'invalidSettingsRejected': 4}, indent=2) + '\n',
        encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; must not exist')
    args = parser.parse_args()
    run(args.engine.resolve(strict=True), args.model.resolve(strict=True), args.output.resolve())
    print('Training data workers: PASS (8 conversion cases and 4 invalid settings)')

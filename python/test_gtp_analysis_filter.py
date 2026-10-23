#!/usr/bin/env python3
"""Check move filtering through real lz-analyze and kata-analyze output.

Use the repository's g170-b6c96 test model with an Eigen build. A two-visit
root cap freezes selection on one child, making sparse reports independent of
CPU speed while the actual asynchronous analysis callbacks keep running.
"""

import argparse
import json
from pathlib import Path
import re
import time

from probe_kg_root_reuse import Probe, file_sha256


def symmetry_orbit(move: str) -> set[str]:
    if move == 'pass':
        return {move}
    columns = 'ABCDEFGHJ'
    x, y = columns.index(move[0]), int(move[1:]) - 1
    return {columns[a] + str(b + 1)
            for a, b in ((x, y), (8-x, y), (x, 8-y), (8-x, 8-y),
                         (y, x), (8-y, x), (y, 8-x), (8-y, 8-x))}


class AnalysisProbe(Probe):
    """Reuse bounded pipe capture and shutdown; check every streamed move list."""
    case = None

    def line(self, deadline, checks=None):
        line = super().line(deadline, checks)
        if not line.startswith('info ') or self.case is None:
            return line
        position, minimum, maximum, symmetry = self.case
        rows = []
        for segment in re.split(r'\binfo\s+', line)[1:]:
            match = re.search(r'^move (\S+) visits (\d+)\b', segment)
            order = re.search(r'\border (\d+)\b', segment)
            pv = re.search(r'\bpv (\S+)', segment)
            assert match and order and pv, line
            assert pv[1] == match[1], ('PV must start with its reported move', line)
            rows.append({'move': match[1], 'visits': int(match[2]), 'order': int(order[1])})
        moves = {row['move'] for row in rows}
        visited = {row['move'] for row in rows if row['visits'] > 0}
        assert len(moves) == len(rows), ('Duplicate moves', self.case, rows)
        assert [row['order'] for row in rows] == list(range(len(rows))), (self.case, rows)
        assert all(move == 'pass' or re.fullmatch(r'[A-HJ][1-9]', move) for move in moves), rows
        assert position != 'center' or 'E5' not in moves, ('Occupied point reported', rows)
        # minmoves is a floor: retain every visited move, then only enough zero-visit
        # moves to fill that floor. maxmoves remains the final output ceiling.
        assert len(rows) == min(maximum, max(minimum, len(visited))), (
            'Surplus zero-visit moves', self.case, rows)
        if visited:
            # The locked model selects the center first on an empty board. With the
            # center occupied, symmetry pruning duplicates the sole searched child.
            if position == 'empty':
                assert visited == {'E5'}, ('Sparse fixture changed', self.case, rows)
            elif symmetry:
                orbit = symmetry_orbit(next(iter(visited)))
                assert len(orbit) in (4, 8), ('Expected noncentral symmetric child', rows)
                assert visited <= orbit, (self.case, rows)
                assert len(visited) == min(maximum, len(orbit)), (
                    'Visited symmetry copies were lost', self.case, rows)
            else:
                assert len(visited) == 1, ('Root cap must keep a single child', self.case, rows)
            self.ready_reports += 1
        self.report_count += 1
        self.rows = rows
        return line

    def check_case(self, command, position, minimum, maximum, symmetry):
        self.command('clear_board')
        if position == 'center':
            self.command(f"play {'W' if self.player == 'B' else 'B'} E5")
        self.case = (position, minimum, maximum, symmetry)
        self.ready_reports = self.report_count = 0
        self.rows = None
        try:
            self.command(f'{command} {self.player} interval 1 minmoves {minimum} maxmoves {maximum}')
            deadline = time.monotonic() + 30
            while self.ready_reports < 2:
                self.line(deadline)
            # Acknowledging a normal GTP command stops analysis. Reports queued before
            # the stop acknowledgement must satisfy the same checks too.
            self.command('name')
            return {'command': command, 'position': position, 'minmoves': minimum,
                    'maxmoves': maximum, 'reportsChecked': self.report_count, 'moves': self.rows}
        finally:
            self.case = None


def run(engine: Path, model: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    identity = {'engineSha256': file_sha256(engine), 'modelSha256': file_sha256(model)}
    results = []
    cases = (
        ('empty', 0, 100),  # No requested placeholders.
        ('empty', 1, 100),
        ('empty', 2, 100),  # One visited move plus exactly one zero-visit move.
        ('empty', 5, 100),  # Keep enough placeholders, including symmetry copies.
        ('empty', 5, 2),    # maxmoves wins when the requested minimum is larger.
        ('empty', 2, 1),
        ('center', 1, 100), # Visited symmetry copies must survive beyond minmoves.
        ('center', 1, 2),   # maxmoves may truncate even visited symmetry copies.
        ('center', 8, 5),
    )
    for player in ('B', 'W'):
        for symmetry in (False, True):
            directory = output / f'{player}-symmetry-{str(symmetry).lower()}'
            directory.mkdir()
            config = directory / 'gtp.cfg'
            config.write_text(
                'rules = chinese\nnumSearchThreads = 1\n'
                f'visitCapContempt = 2\nvisitCapContemptPla = {player}\n'
                f'rootSymmetryPruning = {str(symmetry).lower()}\n'
                'nnRandomize = false\nnnRandSeed = analysis-filter\n'
                'searchRandSeed = analysis-filter\nanalysisWideRootNoise = 0\n'
                'logToStderr = true\nlogAllGTPCommunication = false\nlogSearchInfo = false\n'
                'nnCacheSizePowerOfTwo = 16\nanalysisPVLen = 8\n', encoding='utf-8')
            with ((directory / 'gtp.log').open('x', encoding='utf-8', newline='\n') as transcript,
                  (directory / 'stderr.log').open('x', encoding='utf-8', newline='\n') as stderr):
                probe = AnalysisProbe(engine, model, config, player, False,
                                      transcript=transcript, stderr_log=stderr)
                try:
                    version = probe.command('version')
                    probe.command('boardsize 9')
                    for command in ('lz-analyze', 'kata-analyze'):
                        for position, minimum, maximum in cases:
                            result = probe.check_case(command, position, minimum, maximum, symmetry)
                            results.append({'player': player, 'rootSymmetryPruning': symmetry, **result})
                finally:
                    clean_exit = probe.close()
                assert clean_exit, f'KataGo did not exit cleanly: {probe.process.returncode}'
    # Preserve all logs on failure, but publish PASS only after every process and log closes.
    (output / 'result.json').write_text(json.dumps(
        {'status': 'PASS', 'version': version, **identity, 'cases': results}, indent=2) + '\n',
        encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.engine, args.model, args.output)
    print('GTP analysis filtering: PASS (72 cases)')

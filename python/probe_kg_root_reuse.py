#!/usr/bin/env python3
"""Real-process regression for the opt-in KG-next first-ply tree reuse extension."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time


class Probe:
    def __init__(self, executable: Path, model: Path, config: Path, player: str, ownership: bool):
        self.player = player
        self.ownership = ownership
        self.process = subprocess.Popen(
            [str(executable.resolve()), 'gtp', '-model', str(model.resolve()), '-config', str(config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', bufsize=1,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stderr: list[str] = []
        self.transcript: list[str] = []
        self.serial = 0
        self.last: dict | None = None
        self.reader = threading.Thread(target=self.read_stdout, daemon=True)
        self.err_reader = threading.Thread(target=self.read_stderr, daemon=True)
        self.reader.start()
        self.err_reader.start()

    def read_stdout(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip('\n'))
        self.lines.put(None)

    def read_stderr(self):
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.rstrip('\n'))

    def line(self, deadline: float) -> str:
        try:
            line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty as error:
            raise AssertionError('KataGo response timed out\n' + '\n'.join(self.stderr[-20:])) from error
        if line is None:
            raise AssertionError('KataGo exited\n' + '\n'.join(self.stderr[-20:]))
        self.transcript.append('< ' + line)
        if line.startswith('info '):
            sections = re.split(r'\brootInfo\s+', line, maxsplit=1)
            root = re.search(r'\bvisits\s+(\d+)', sections[1]) if len(sections) == 2 else None
            moves = {}
            details = {}
            for segment in re.split(r'\binfo\s+', sections[0])[1:]:
                move = re.search(r'\bmove\s+(\S+)', segment)
                visits = re.search(r'\bvisits\s+(\d+)', segment)
                if move and visits:
                    moves[move[1]] = int(visits[1])
                    details[move[1]] = self.fields(segment)
                    if self.ownership:
                        self.check_ownership(segment, 'movesOwnership')
            if root:
                if self.ownership:
                    self.check_ownership(sections[1], 'ownership')
                self.last = {'visits': int(root[1]), 'moves': moves,
                             'moveDetails': details, 'root': self.fields(sections[1])}
        return line

    @staticmethod
    def fields(segment: str) -> dict:
        result = {}
        for key in ['winrate', 'scoreLead', 'weight', 'utility']:
            match = re.search(r'\b' + key + r'\s+(\S+)', segment)
            if match:
                result[key] = float(match[1])
                assert math.isfinite(result[key]), (key, segment)
        edge = re.search(r'\bpvEdgeVisits\s+(\d+)', segment)
        if edge:
            result['edgeVisits'] = int(edge[1])
        symmetry = re.search(r'\bisSymmetryOf\s+(\S+)', segment)
        if symmetry:
            result['isSymmetryOf'] = symmetry[1]
        return result

    @staticmethod
    def check_ownership(segment: str, key: str):
        match = re.search(r'\b' + key + r'\s+([-+0-9.eE ]+)', segment)
        assert match, f'Missing {key}: {segment}'
        values = [float(value) for value in match[1].split()]
        assert len(values) == 81, (key, len(values))
        assert all(math.isfinite(value) and -1.000001 <= value <= 1.000001 for value in values), key

    def command(self, text: str, timeout: float = 90) -> str:
        self.serial += 1
        serial = self.serial
        self.transcript.append(f'> {serial} {text}')
        assert self.process.stdin is not None
        self.process.stdin.write(f'{serial} {text}\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        response = []
        started = False
        while time.monotonic() < deadline:
            line = self.line(deadline)
            if line.startswith(f'?{serial}'):
                raise AssertionError(f'{text}: {line}')
            if line.startswith(f'={serial}'):
                started = True
                response.append(line[len(str(serial)) + 1:].strip())
                if text.startswith('kata-analyze'):
                    return '\n'.join(response)
            elif started:
                if not line:
                    return '\n'.join(response)
                response.append(line)
        raise AssertionError(f'Timed out waiting for {text}')

    def search(self, restrictions: str, minimum: int, previous: int | None = None,
               allowed: set[str] | None = None, forbidden: set[str] | None = None,
               reuse: bool = True) -> dict:
        self.last = None
        self.command(f'kata-analyze {self.player} interval 1 rootInfo true pvEdgeVisits true minmoves 30 '
                     + ('ownership true movesOwnership true ' if self.ownership else '')
                     + ('reuseRootTree true ' if reuse else '') + restrictions)
        deadline = time.monotonic() + 90
        first = None
        while time.monotonic() < deadline:
            self.line(deadline)
            if self.last is None:
                continue
            current = self.last
            if first is None:
                first = current['visits']
                if previous is not None:
                    assert first >= previous, f'Tree reset: {first} < {previous} ({restrictions})'
            if allowed is not None:
                assert set(current['moves']) <= allowed, (restrictions, current)
            if forbidden is not None:
                assert not set(current['moves']) & forbidden, (restrictions, current)
            if current['visits'] >= minimum:
                self.command('name')  # Acknowledged barrier consumes final old analysis before the next request.
                assert self.last is not None
                return {**self.last, 'firstVisits': first, 'restriction': restrictions}
        raise AssertionError('Search visit target timed out')

    def close(self):
        if self.process.poll() is None:
            try:
                self.command('quit', timeout=5)
            except (AssertionError, OSError):
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.err_reader.join(timeout=5)


def run(executable: Path, model: Path, output: Path, player: str = "B", threads: int = 1,
        ownership: bool = False, root_visit_cap: int = 0):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kg-root-reuse-') as directory:
        config = Path(directory) / 'gtp.cfg'
        config.write_text(
            f'rules = chinese\nnumSearchThreads = {threads}\nmaxVisits = 1000000\n'
            'maxTime = 1000000000\nrootSymmetryPruning = true\n'
            'logToStderr = true\nlogAllGTPCommunication = false\nlogSearchInfo = false\n'
            'reportAnalysisWinratesAs = BLACK\nanalysisPVLen = 8\n'
            'nnCacheSizePowerOfTwo = 16\n'
            f'visitCapContempt = {root_visit_cap}\nvisitCapContemptPla = {player}\n', encoding='utf-8')
        probe = Probe(executable, model, config, player, ownership)
        results = []
        try:
            assert probe.command('known_command kg-reuse-root-tree').strip() == 'true'
            assert probe.command('kg-reuse-root-tree').strip() == 'root-only-v1'
            version = probe.command('version')
            probe.command('boardsize 9')
            base = probe.search('', 1500)
            results.append(base)
            searched_moves = {move: visits for move, visits in base['moves'].items()
                              if 'isSymmetryOf' not in base['moveDetails'][move] and visits > 0}
            selected = max(searched_moves, key=searched_moves.get)
            restricted = probe.search(f'allow b {selected} 1 allow w {selected} 1',
                                      base['visits'] + 250, base['visits'], allowed={selected})
            assert restricted['moves'][selected] >= base['moves'][selected]
            child_weight = restricted['moveDetails'][selected]['weight']
            # With one allowed child, its edge weight and the root NN evaluation are the only
            # contributors. Reporting may race with the search by a few parallel playouts.
            assert restricted['root']['weight'] <= child_weight + 3 * threads + 1, restricted
            assert abs(restricted['root']['winrate'] - restricted['moveDetails'][selected]['winrate']) < 0.05, restricted
            results.append(restricted)
            avoided = probe.search(f'avoid b {selected} 1 avoid w {selected} 1',
                                   restricted['visits'] + 250, restricted['visits'], forbidden={selected})
            results.append(avoided)
            restored = probe.search('', avoided['visits'] + 150, avoided['visits'])
            assert restored['moves'].get(selected, 0) >= restricted['moves'][selected]
            results.append(restored)
            opposite = 'w' if player == 'B' else 'b'
            inactive = probe.search(f'avoid {opposite} {selected} 1',
                                    restored['visits'] + 50, restored['visits'])
            assert inactive['moves'].get(selected, 0) >= restricted['moves'][selected]
            results.append(inactive)
            # A formerly symmetry-pruned / unvisited action and pass must remain selectable.
            current = inactive
            # Include a low-policy move absent from a root visit-cap snapshot, as well as pass.
            for move in ['D4', 'F6', 'A1', 'pass']:
                current = probe.search(f'allow b {move} 1 allow w {move} 1',
                                       current['visits'] + 50, current['visits'], allowed={move})
                assert current['moves'].get(move, 0) > 0
                results.append(current)
            # A focus target excluded by the mask must never get redirected root playouts.
            focused = probe.search(f'allow b pass 1 allow w pass 1 focus {selected} 0.9',
                                   current['visits'] + 50, current['visits'], allowed={'pass'})
            results.append(focused)
            focused_edge = focused['moveDetails']['pass']['edgeVisits']
            assert focused_edge > current['moveDetails']['pass']['edgeVisits']
            current = focused
            cleared = probe.search('', current['visits'] + 100, current['visits'])
            assert cleared['moves'].get(selected, 0) >= restricted['moves'][selected]
            results.append(cleared)
            deep = probe.search(f'avoid b {selected} 2', 1000)
            assert deep['firstVisits'] < cleared['visits'], 'Deeper restrictions must invalidate child evaluations'
            results.append(deep)
            shallow = probe.search('', 1500)
            assert shallow['firstVisits'] < deep['visits'], 'Removing deeper restrictions must also invalidate the tree'
            results.append(shallow)
            # An unmodified caller without the extension retains the upstream clearing contract.
            legacy = probe.search(f'avoid b {selected} 1', 100, reuse=False)
            assert legacy['firstVisits'] < shallow['visits']
            results.append(legacy)
            probe.command('clear_cache')
            fresh = probe.search('', 50)
            assert fresh['firstVisits'] < shallow['visits']
            results.append(fresh)
            (output / 'result.json').write_text(json.dumps(
                {'status': 'PASS', 'version': version, 'model': model.name, 'player': player,
                 'threads': threads, 'ownership': ownership, 'rootVisitCap': root_visit_cap, 'cases': results},
                ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        finally:
            probe.close()
            (output / 'gtp.log').write_text('\n'.join(probe.transcript) + '\n', encoding='utf-8')
            (output / 'stderr.log').write_text('\n'.join(probe.stderr) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--player', choices=['B', 'W'], default='B')
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--ownership', action='store_true')
    parser.add_argument('--root-visit-cap', type=int, default=0)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('--threads must be positive')
    if args.root_visit_cap < 0 or args.root_visit_cap == 1:
        parser.error('--root-visit-cap must be 0 or at least 2')
    run(args.engine, args.model, args.output, args.player, args.threads, args.ownership, args.root_visit_cap)
    print('KG-next root tree reuse: PASS')


if __name__ == '__main__':
    main()

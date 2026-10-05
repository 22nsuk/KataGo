#!/usr/bin/env python3
"""Real-process regression for the opt-in KG-next first-ply tree reuse extension."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time


VISIT_BUDGET = 64


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
            self.check_moves(current, restrictions, allowed, forbidden)
            if current['visits'] >= minimum:
                self.command('name')  # Acknowledged barrier consumes final old analysis before the next request.
                assert self.last is not None
                # The barrier may consume additional reports. Validate the actual returned
                # snapshot too, not just the report that first reached the visit target.
                assert self.last['visits'] >= current['visits'], (restrictions, current, self.last)
                self.check_moves(self.last, restrictions, allowed, forbidden)
                return {**self.last, 'firstVisits': first, 'restriction': restrictions}
        raise AssertionError('Search visit target timed out')

    @staticmethod
    def check_moves(result: dict, restrictions: str, allowed: set[str] | None,
                    forbidden: set[str] | None):
        if allowed is not None:
            assert set(result['moves']) <= allowed, (restrictions, result)
        if forbidden is not None:
            assert not set(result['moves']) & forbidden, (restrictions, result)

    def bounded_search(self, restrictions: str, reuse: bool = True) -> dict:
        self.last = None
        self.command(f'kata-search_analyze {self.player} interval 1 rootInfo true pvEdgeVisits true minmoves 30 '
                     + ('ownership true movesOwnership true ' if self.ownership else '')
                     + ('reuseRootTree true ' if reuse else '') + restrictions)
        assert self.last is not None, ('Missing bounded analysis', restrictions)
        return {**self.last, 'restriction': f'bounded {restrictions}'}

    def warm_bounded(self, restrictions: str, minimum: int, previous: int | None = None,
                     reuse: bool = True) -> dict:
        while True:
            current = self.bounded_search(restrictions, reuse)
            if previous is not None:
                assert current['visits'] >= previous + VISIT_BUDGET, (
                    'Unchanged bounded restriction must retain the tree and add new playouts',
                    restrictions, previous, current)
            if current['visits'] >= minimum:
                return current
            previous = current['visits']

    def close(self) -> bool:
        clean_exit = self.process.poll() is None
        if clean_exit:
            try:
                self.command('quit', timeout=5)
            except (AssertionError, OSError):
                clean_exit = False
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                clean_exit = False
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.err_reader.join(timeout=5)
        readers_stopped = not self.reader.is_alive() and not self.err_reader.is_alive()
        if readers_stopped:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()
        return clean_exit and self.process.returncode == 0 and readers_stopped


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run(executable: Path, model: Path, output: Path, player: str = "B", threads: int = 1,
        ownership: bool = False, root_visit_cap: int = 0):
    # Never let a failed rerun inherit a previous PASS or mix logs from different runs.
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='kg-root-reuse-') as directory:
        config = Path(directory) / 'gtp.cfg'
        config.write_text(
            f'rules = chinese\nnumSearchThreads = {threads}\nmaxPlayouts = {VISIT_BUDGET}\n'
            'maxTime = 1000000000\nrootSymmetryPruning = true\n'
            'logToStderr = true\nlogAllGTPCommunication = false\nlogSearchInfo = false\n'
            'reportAnalysisWinratesAs = BLACK\nanalysisPVLen = 8\n'
            'nnCacheSizePowerOfTwo = 16\n'
            f'visitCapContempt = {root_visit_cap}\nvisitCapContemptPla = {player}\n', encoding='utf-8')
        (output / 'gtp.cfg').write_bytes(config.read_bytes())
        identity = {'engineSha256': file_sha256(executable), 'modelSha256': file_sha256(model),
                    'configSha256': file_sha256(config)}
        probe = Probe(executable, model, config, player, ownership)
        results = []
        try:
            assert probe.command('known_command kg-reuse-root-tree').strip() == 'true'
            assert probe.command('kg-reuse-root-tree').strip() == 'root-only-v1'
            version = probe.command('version')
            probe.command('boardsize 9')
            # A first restricted analysis has no tree to preserve. Avoiding pass leaves
            # board symmetries intact, so analysis must still report symmetric copies.
            fresh_restricted = probe.search('avoid b pass 1 avoid w pass 1', 300, forbidden={'pass'})
            assert any('isSymmetryOf' in detail for detail in fresh_restricted['moveDetails'].values()), fresh_restricted
            results.append(fresh_restricted)
            probe.command('clear_cache')
            # Keep these transitions bounded so every request has the same parameters
            # and retained representative edge visits can be compared at a stopped barrier.
            symmetric = probe.warm_bounded('', 1000)
            results.append(symmetric)
            original_representatives = {move: detail for move, detail in symmetric['moveDetails'].items()
                                        if 'isSymmetryOf' not in detail and symmetric['moves'][move] > 0
                                        and 'edgeVisits' in detail}
            original_aliases = {move: detail['isSymmetryOf']
                                for move, detail in symmetric['moveDetails'].items()
                                if detail.get('isSymmetryOf') in original_representatives}
            assert original_aliases, symmetric
            representative = max(set(original_aliases.values()), key=lambda move: symmetric['moves'][move])
            duplicate = next(move for move, real_move in original_aliases.items()
                             if real_move == representative)
            active = player.lower()
            opposite = 'w' if player == 'B' else 'b'
            previous = symmetric
            for label, restrictions, forbidden in [
                ('pass-only', f'avoid {active} pass 1', {'pass'}),
                ('clear-pass-only', '', set()),
                ('inactive-color', f'avoid {opposite} {representative} 1', set()),
                ('nonrepresentative', f'avoid {active} {duplicate} 1', {duplicate}),
                ('clear-nonrepresentative', '', set()),
            ]:
                current = probe.bounded_search(restrictions)
                assert current['visits'] >= previous['visits'] + VISIT_BUDGET, (label, previous, current)
                assert not set(current['moves']) & forbidden, (label, current)
                aliases = {move: detail['isSymmetryOf']
                           for move, detail in current['moveDetails'].items()
                           if detail.get('isSymmetryOf') in original_representatives}
                assert aliases == {move: real_move for move, real_move in original_aliases.items()
                                   if move not in forbidden}, (label, original_aliases, current)
                for move, detail in original_representatives.items():
                    if move not in forbidden:
                        assert move in current['moveDetails'], (label, move, current)
                        assert 'isSymmetryOf' not in current['moveDetails'][move], (label, move, current)
                        assert current['moveDetails'][move]['edgeVisits'] >= detail['edgeVisits'], (label, move, current)
                results.append({**current, 'symmetryTransition': label})
                previous = current
            # A real representative change still disables pruning and retains the hidden
            # child. Clearing its restriction must restore its accumulated edge visits.
            changed = probe.bounded_search(f'avoid {active} {representative} 1')
            assert changed['visits'] >= previous['visits'] + VISIT_BUDGET, changed
            assert representative not in changed['moves'], changed
            assert all('isSymmetryOf' not in detail for detail in changed['moveDetails'].values()), changed
            results.append({**changed, 'symmetryTransition': 'representative-change'})
            restored_symmetric = probe.bounded_search('')
            assert restored_symmetric['visits'] >= changed['visits'] + VISIT_BUDGET, restored_symmetric
            assert restored_symmetric['moveDetails'][representative]['edgeVisits'] >= previous['moveDetails'][representative]['edgeVisits'], restored_symmetric
            assert all('isSymmetryOf' not in detail for detail in restored_symmetric['moveDetails'].values()), restored_symmetric
            results.append({**restored_symmetric, 'symmetryTransition': 'sticky-disable-after-clear'})
            probe.command('clear_cache')
            fresh_symmetric = probe.bounded_search('')
            assert VISIT_BUDGET <= fresh_symmetric['visits'] <= VISIT_BUDGET + threads, fresh_symmetric
            assert any('isSymmetryOf' in detail for detail in fresh_symmetric['moveDetails'].values()), fresh_symmetric
            results.append({**fresh_symmetric, 'symmetryTransition': 'clear-cache-reset'})
            probe.command('clear_cache')
            single = probe.search('allow b D4 1 allow w D4 1', 300, allowed={'D4'})
            mixed = probe.search('allow b D4,F6 1 allow w D4,F6 1',
                                 single['visits'] + 250, single['visits'], allowed={'D4', 'F6'})
            # A retained allowed child must not freeze a newly allowed child out of a cap.
            assert mixed['moveDetails']['D4']['edgeVisits'] >= single['moveDetails']['D4']['edgeVisits']
            assert mixed['moveDetails']['F6']['edgeVisits'] > 0, mixed
            results.extend([single, mixed])
            probe.command('clear_cache')
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

            # First timed reports can exceed an old tree's visits on a warm GPU. Keep
            # every reset and its predecessor in bounded mode with identical parameters:
            # switching from kata-analyze to kata-search_analyze itself clears the tree.
            reset_upper_bound = VISIT_BUDGET + threads
            warm_minimum = max(1000, reset_upper_bound + 1)
            warm = probe.warm_bounded('', warm_minimum)
            results.append(warm)

            def bounded_reset(restrictions: str, previous: dict, reason: str,
                              reuse: bool = True) -> dict:
                assert previous['visits'] > reset_upper_bound, ('Reset predecessor must be warm', previous)
                result = probe.bounded_search(restrictions, reuse)
                # Allow for in-flight parallel playouts at the stop check. A retained
                # warm tree cannot fit within this bound.
                assert VISIT_BUDGET <= result['visits'] <= reset_upper_bound, (reason, previous, result)
                results.append({**result, 'resetFromVisits': previous['visits'], 'resetReason': reason})
                return result

            deep = bounded_reset(f'avoid b {selected} 2', warm,
                                 'Deeper restrictions must invalidate child evaluations')
            deep = probe.warm_bounded(f'avoid b {selected} 2', warm_minimum, deep['visits'])
            results.append(deep)
            shallow = bounded_reset('', deep, 'Removing deeper restrictions must also invalidate the tree')
            shallow = probe.warm_bounded('', max(1500, warm_minimum), shallow['visits'])
            results.append(shallow)
            # An unmodified caller without the extension retains the upstream clearing contract.
            legacy = bounded_reset(f'avoid b {selected} 1', shallow,
                                   'Legacy changed restrictions must invalidate the tree', reuse=False)
            legacy = probe.warm_bounded(f'avoid b {selected} 1', warm_minimum, legacy['visits'], reuse=False)
            results.append(legacy)
            probe.command('clear_cache')
            bounded_reset('', legacy, 'clear_cache must invalidate the tree')
            # Changed komi, rules, or player must still invalidate a reused restricted root.
            for command in ['komi 8.5', 'kata-set-rules japanese']:
                restrictions = f'avoid {active} pass 1'
                previous = probe.warm_bounded(restrictions, warm_minimum)
                probe.command(command)
                bounded_reset(restrictions, previous, command)
            previous = probe.warm_bounded('', warm_minimum)
            probe.player = 'W' if player == 'B' else 'B'
            bounded_reset('', previous, 'Changed analysis player')
            probe.player = player
            probe.command('kata-set-rules chinese')
            # Continuous kata-analyze above ignores configured move limits. Bounded
            # warmups obeyed maxPlayouts' new-work limit. Only now change parameters
            # and clear the tree to check maxVisits' lifetime-counting semantics.
            probe.command(f'kata-set-param maxVisits {VISIT_BUDGET}')
            probe.command('clear_cache')
            bounded = []
            for move in ['D4', 'F6']:
                bounded.append(probe.bounded_search(f'allow b {move} 1 allow w {move} 1'))
            assert bounded[0]['visits'] >= VISIT_BUDGET, bounded
            assert bounded[1]['visits'] == bounded[0]['visits'], bounded
            assert bounded[1]['moves'].get('F6', 0) == 0, bounded
            results.extend(bounded)
            # Restriction-free, empty-list, and inactive-color encodings must preserve
            # a capped root without inventing a new bounded-search budget.
            probe.command('clear_cache')
            unchanged = probe.bounded_search('')
            for restrictions in [f'avoid {active} , 1', f'avoid {opposite} D4 1', '']:
                equivalent = probe.bounded_search(restrictions)
                assert equivalent['visits'] == unchanged['visits'], (unchanged, equivalent)
                assert equivalent['moves'] == unchanged['moves'], (unchanged, equivalent)
                results.append({**equivalent, 'equivalentRootMask': True})
                unchanged = equivalent
        finally:
            try:
                clean_exit = probe.close()
            finally:
                (output / 'gtp.log').write_text('\n'.join(probe.transcript) + '\n', encoding='utf-8')
                (output / 'stderr.log').write_text('\n'.join(probe.stderr) + '\n', encoding='utf-8')
        assert clean_exit, f'KataGo did not exit cleanly: {probe.process.returncode}'
        # Success includes acknowledged shutdown and saved logs, not only search checks.
        (output / 'result.json').write_text(json.dumps(
            {'status': 'PASS', 'version': version, 'model': model.name, 'player': player,
             'threads': threads, 'ownership': ownership, 'rootVisitCap': root_visit_cap,
             **identity, 'engineReturnCode': probe.process.returncode, 'cases': results},
            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


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

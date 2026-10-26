#!/usr/bin/env python3
"""Check parameter command discovery and JSON updates through a real GTP engine."""

import argparse
import json
import math
from pathlib import Path
import re

from probe_kg_root_reuse import Probe, file_sha256


def snapshot(probe, name):
    state = json.loads(probe.command(f'kata-get-param-state {name}'))
    assert set(state) == {'value', 'configuredValue'}, state
    assert type(state['value']) in (int, float) and math.isfinite(state['value']), state
    assert state['configuredValue'] is None or isinstance(state['configuredValue'], str), state
    return state


def rejected(probe, command):
    try:
        probe.command(command)
    except AssertionError as error:
        assert str(error).startswith(command + ': ?'), str(error)
    else:
        raise AssertionError(f'Expected a GTP error: {command}')


def check_restoration(engine, model, output):
    # Both presence bits matter. Indexed keys are intentionally unused by the GTP loader.
    cases = (
        ('automatic', None, None, None),
        ('static-zero', '0', None, None),
        ('explicit-dynamic', None, '0.045', None),
        ('dynamic-with-static-value', '1.25000e-1', '0.045', None),
        ('indexed-automatic', None, None, '0.75'),
        ('indexed-static', '1.25000e-1', None, '0.75'),
    )
    pda = 'playoutDoublingAdvantage'
    wrn = 'analysisWideRootNoise'
    for name, configured, cap, indexed in cases:
        case = output / name
        case.mkdir()
        config = case / 'gtp.cfg'
        settings = (
            'rules = chinese\nnumSearchThreads = 1\nmaxVisits = 2\n'
            'nnCacheSizePowerOfTwo = 16\nlogToStderr = true\n'
            'logAllGTPCommunication = false\nlogSearchInfo = false\nogsChatToStderr = true\n'
        )
        if configured is not None:
            settings += f'{pda} = {configured}\n'
            settings += f'{wrn} = 1.23456789012345\n'
        if cap is not None:
            settings += f'dynamicPlayoutDoublingAdvantageCapPerOppLead = {cap}\n'
        if indexed is not None:
            settings += f'{pda}0 = {indexed}\n'
        config.write_text(settings, encoding='utf-8')
        with ((case / 'gtp.log').open('x', encoding='utf-8') as transcript,
              (case / 'stderr.log').open('x', encoding='utf-8') as stderr):
            probe = Probe(engine, model, config, 'W', False, transcript=transcript, stderr_log=stderr)
            try:
                original = {key: snapshot(probe, key) for key in (pda, wrn)}
                assert original[pda] == {
                    'value': float(configured or '0'), 'configuredValue': configured}, original
                assert original[wrn]['configuredValue'] == (
                    None if configured is None else '1.23456789012345'), original
                probe.command('boardsize 9')
                probe.command('komi 0')
                probe.command('fixed_handicap 4')
                probe.command('genmove W')

                updates = {pda: '-1.23456789012345', wrn: '2.34567890123456'}
                probe.command('kata-set-params ' + json.dumps(updates))
                changed = {key: snapshot(probe, key) for key in (pda, wrn)}
                for key in (pda, wrn):
                    assert changed[key]['configuredValue'] == updates[key], changed
                    assert changed[key]['value'] == float(updates[key]), changed
                # The old WRN getter is intentionally unchanged, while the snapshot preserves precision.
                assert float(probe.command(f'kata-get-param {wrn}')) != changed[wrn]['value']
                for command in (
                    'kata-get-param-state', 'kata-get-param-state maxVisits',
                    f'kata-get-param-state {pda} extra', f'kata-get-param-state {pda}0',
                    f'kata-set-param {pda} nan',
                    'kata-set-params {"playoutDoublingAdvantage":"0.5","analysisWideRootNoise":"9"}',
                    'kata-set-params {',
                ):
                    rejected(probe, command)
                    assert {key: snapshot(probe, key) for key in (pda, wrn)} == changed

                restore = {key: state['configuredValue'] or '' for key, state in original.items()}
                probe.command('kata-set-params ' + json.dumps(restore))
                assert {key: snapshot(probe, key) for key in (pda, wrn)} == original
                probe.command('clear_board')
                probe.command('fixed_handicap 4')
                probe.command('genmove W')
            finally:
                clean_exit = probe.close()
            assert clean_exit, (name, probe.process.returncode)
        # Observe the actual search PDA rather than merely inferring mode from the snapshot.
        chats = [line for line in (case / 'stderr.log').read_text(encoding='utf-8').splitlines()
                 if line.startswith('MALKOVICH:')]
        assert len(chats) == 2, (name, chats)
        actual = [float(match[1]) if (match := re.search(r'\(PDA ([-.0-9]+)\)', line)) else 0.0
                  for line in chats]
        assert actual[0] == actual[1], (name, actual)
        if configured is None or cap is not None:
            assert actual[0] > 0, ('Dynamic PDA was disabled', name, actual)
        else:
            assert actual[0] == round(float(configured), 2), (name, actual)
    return [case[0] for case in cases]


def run(engine: Path, model: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    identity = {'engineSha256': file_sha256(engine), 'modelSha256': file_sha256(model)}
    config = output / 'gtp.cfg'
    config.write_text(
        'rules = chinese\nnumSearchThreads = 1\nmaxVisits = 100\nmaxPlayouts = 200\n'
        'nnCacheSizePowerOfTwo = 16\nlogToStderr = true\n'
        'logAllGTPCommunication = false\nlogSearchInfo = false\n', encoding='utf-8')
    commands = ('kata-get-param', 'kata-get-param-state', 'kata-get-params', 'kata-set-param', 'kata-set-params')
    with ((output / 'gtp.log').open('x', encoding='utf-8', newline='\n') as transcript,
          (output / 'stderr.log').open('x', encoding='utf-8', newline='\n') as stderr):
        probe = Probe(engine, model, config, 'B', False, transcript=transcript, stderr_log=stderr)
        try:
            version = probe.command('version')
            listed = probe.command('list_commands').splitlines()
            known = {command: probe.command(f'known_command {command}').strip()
                     for command in commands}
            assert known == dict.fromkeys(commands, 'true'), ('Missing known commands', known)
            for command in commands:
                assert listed.count(command) == 1, ('Missing or duplicate listed command', command, listed)
            unknown = 'kata-nonexistent-param-command'
            assert probe.command(f'known_command {unknown}').strip() == 'false'
            assert unknown not in listed

            before = json.loads(probe.command('kata-get-params'))
            assert before['maxVisits'] == 100 and before['maxPlayouts'] == 200, before
            updates = {'maxVisits': 20, 'maxPlayouts': 30}
            assert probe.command('kata-set-params ' + json.dumps(updates)).strip() == ''
            after = json.loads(probe.command('kata-get-params'))
            for key, value in updates.items():
                assert after[key] == value, (key, after)
                assert json.loads(probe.command(f'kata-get-param {key}')) == value
            assert probe.command('kata-set-param maxVisits 21').strip() == ''
            assert json.loads(probe.command('kata-get-params'))['maxVisits'] == 21
        finally:
            clean_exit = probe.close()
        assert clean_exit, f'KataGo did not exit cleanly: {probe.process.returncode}'
    restoration_cases = check_restoration(engine, model, output)
    # Record success only after the protocol checks, shutdown, and log writes succeed.
    (output / 'result.json').write_text(json.dumps(
        {'status': 'PASS', 'version': version, **identity, 'knownCommands': known,
         'updatedParameters': updates, 'restorationCases': restoration_cases}, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.engine, args.model, args.output)
    print('GTP parameter commands: PASS (discovery, JSON updates, exact state and PDA mode restoration)')

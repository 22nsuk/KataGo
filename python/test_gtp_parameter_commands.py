#!/usr/bin/env python3
"""Check parameter command discovery and JSON updates through a real GTP engine."""

import argparse
import json
from pathlib import Path

from probe_kg_root_reuse import Probe, file_sha256


def run(engine: Path, model: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    identity = {'engineSha256': file_sha256(engine), 'modelSha256': file_sha256(model)}
    config = output / 'gtp.cfg'
    config.write_text(
        'rules = chinese\nnumSearchThreads = 1\nmaxVisits = 100\nmaxPlayouts = 200\n'
        'nnCacheSizePowerOfTwo = 16\nlogToStderr = true\n'
        'logAllGTPCommunication = false\nlogSearchInfo = false\n', encoding='utf-8')
    commands = ('kata-get-param', 'kata-get-params', 'kata-set-param', 'kata-set-params')
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
    # Record success only after the protocol checks, shutdown, and log writes succeed.
    (output / 'result.json').write_text(json.dumps(
        {'status': 'PASS', 'version': version, **identity, 'knownCommands': known,
         'updatedParameters': updates}, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.engine, args.model, args.output)
    print('GTP parameter commands: PASS (discovery and JSON round trip)')

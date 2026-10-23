#!/usr/bin/env python3
"""Select CI checks from the complete PR or push change set.

Keep the workflow and job check names visible. Unknown events, missing history,
invalid event data and empty diffs conservatively retain the native build matrix.
"""
from __future__ import annotations

import json
from fnmatch import fnmatchcase
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Iterable


BOARD_PATHS = (
    'python/katago/game/**', 'python/tests/test_board_zobrist.py',
    'conftest.py', 'python/conftest.py', 'python/tests/conftest.py',
    'pytest.ini', 'python/pytest.ini',
)
CUDA_PATHS = (
    'cpp/**', 'scripts/windows_cuda_sdk.py', 'scripts/build_windows_cuda_bundle.py',
    'scripts/windows_cuda*.lock.json', 'scripts/test_windows_cuda_bundle.py',
)


def all_checks(event_name: str) -> dict[str, bool]:
    # CUDA packages remain a PR/manual release check, not a second build on merge.
    return {'native': True, 'onnx': True, 'cuda': event_name != 'push', 'board': True}


def documentation_only(paths: Iterable[str]) -> bool:
    paths = list(paths)
    if not paths:
        return False
    for name in paths:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            return False
        markdown = path.suffix == '.md' and (len(path.parts) == 1 or path.parts[0] == 'docs')
        if not markdown and name not in {'LICENSE', 'CONTRIBUTORS'}:
            return False
    return True


def select_checks(event_name: str, event: dict, cwd: Path | None = None) -> tuple[dict[str, bool], str]:
    if event_name not in {'pull_request', 'push'}:
        return all_checks(event_name), 'manual or unrecognized event'
    try:
        if event_name == 'pull_request':
            base = event['pull_request']['base']['sha']
            head = event['pull_request']['head']['sha']
        else:
            base, head = event['before'], event['after']
        if any(not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha)
               or sha == '0' * 40 for sha in (base, head)):
            return all_checks(event_name), 'missing or invalid commit identity'
        # PRs compare authored changes; pushes compare the complete before/after trees.
        revision_args = [f'{base}...{head}'] if event_name == 'pull_request' else [base, head]
        data = subprocess.check_output(
            ['git', 'diff', '--name-only', '--no-renames', '-z', *revision_args, '--'],
            cwd=cwd, stderr=subprocess.PIPE,
        )
        paths = [os.fsdecode(name) for name in data.split(b'\0') if name]
    except (KeyError, TypeError, OSError, subprocess.CalledProcessError):
        return all_checks(event_name), 'change set unavailable; keep coverage'
    if documentation_only(paths):
        return dict.fromkeys(all_checks(event_name), False), 'documentation-only change set'
    if not paths or any(name.startswith(('.github/', 'scripts/ci_', 'scripts/test_ci_')) for name in paths):
        return all_checks(event_name), 'CI policy or empty change set; keep coverage'
    return {
        'native': True,
        'onnx': any(name.startswith('cpp/') for name in paths),
        'cuda': event_name != 'push' and any(fnmatchcase(name, pattern) for name in paths for pattern in CUDA_PATHS),
        'board': any(fnmatchcase(name, pattern) for name in paths for pattern in BOARD_PATHS),
    }, 'code change set; native coverage plus affected backend and board checks'


def main() -> None:
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        checks, reason = select_checks(os.environ.get('GITHUB_EVENT_NAME', ''), event)
    except (KeyError, OSError, ValueError):
        checks, reason = all_checks(os.environ.get('GITHUB_EVENT_NAME', '')), 'event unavailable; keep coverage'
    result = ''.join(f'{name}={str(selected).lower()}\n' for name, selected in checks.items())
    print(result + reason)
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(result)


if __name__ == '__main__':
    main()

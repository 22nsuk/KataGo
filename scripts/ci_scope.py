#!/usr/bin/env python3
"""Skip native builds only for a proven documentation-only change set.

Keep the workflow and job check names visible. Unknown events, missing history,
invalid event data and empty diffs conservatively retain the native build matrix.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Iterable


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


def needs_native(event_name: str, event: dict, cwd: Path | None = None) -> tuple[bool, str]:
    if event_name not in {'pull_request', 'push'}:
        return True, 'manual or unrecognized event'
    try:
        if event_name == 'pull_request':
            base = event['pull_request']['base']['sha']
            head = event['pull_request']['head']['sha']
        else:
            base, head = event['before'], event['after']
        if any(not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha)
               or sha == '0' * 40 for sha in (base, head)):
            return True, 'missing or invalid commit identity'
        # PRs compare authored changes; pushes compare the complete before/after trees.
        revision_args = [f'{base}...{head}'] if event_name == 'pull_request' else [base, head]
        data = subprocess.check_output(
            ['git', 'diff', '--name-only', '--no-renames', '-z', *revision_args, '--'],
            cwd=cwd, stderr=subprocess.PIPE,
        )
        paths = [os.fsdecode(name) for name in data.split(b'\0') if name]
    except (KeyError, TypeError, OSError, subprocess.CalledProcessError):
        return True, 'change set unavailable; keep native coverage'
    if documentation_only(paths):
        return False, 'documentation-only change set'
    return True, 'code, CI, unknown files, or empty change set'


def main() -> None:
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        native, reason = needs_native(os.environ.get('GITHUB_EVENT_NAME', ''), event)
    except (KeyError, OSError, ValueError):
        native, reason = True, 'event unavailable; keep native coverage'
    result = f'native={str(native).lower()}\n'
    print(result + reason)
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(result)


if __name__ == '__main__':
    main()

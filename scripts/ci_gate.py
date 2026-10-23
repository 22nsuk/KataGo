#!/usr/bin/env python3
"""Fail the required check if a selected CI job failed, was cancelled, or did not run."""
import json
import os


SELECTED_JOBS = ('engine', 'onnx', 'cuda', 'board')


def validate(needs: dict) -> None:
    expected_jobs = {'plan', 'general', *SELECTED_JOBS}
    if not isinstance(needs, dict) or set(needs) != expected_jobs:
        raise ValueError('Missing or unexpected CI job results')
    if any(not isinstance(job, dict) for job in needs.values()):
        raise ValueError('Invalid CI job results')
    if needs['plan'].get('result') != 'success':
        raise ValueError('CI scope selection did not succeed')
    outputs = needs['plan'].get('outputs')
    if not isinstance(outputs, dict) or any(outputs.get(job) not in ('true', 'false') for job in SELECTED_JOBS):
        raise ValueError('CI scope selection is missing or invalid')
    if needs['general'].get('result') != 'success':
        raise ValueError('General build and policy checks did not succeed')
    for job in SELECTED_JOBS:
        result = needs[job].get('result')
        if result == 'success':
            continue
        if result == 'skipped' and outputs[job] == 'false':
            continue
        raise ValueError(f'{job} must succeed (selected={outputs[job]}, result={result})')


if __name__ == '__main__':
    validate(json.loads(os.environ['CI_NEEDS']))
    print('All selected CI checks passed')

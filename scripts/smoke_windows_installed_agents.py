#!/usr/bin/env python3
"""CI-only contracts against real Windows npm installs, without provider sign-in."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/src'))

from job_finder.providers import AgentRunner, ProviderError, inspect_providers


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Run only on a disposable Windows GitHub Actions runner without agent credentials.')

    for provider in ('codex', 'claude'):
        os.environ[f'JOB_FINDER_{provider.upper()}_BIN'] = str(args.prefix / (provider + '.cmd'))
    # Simulate Explorer remaining open across CLI installation.
    os.environ['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    for status in inspect_providers():
        assert status['available'] and status['version'], status
        assert status['path'].endswith('.exe'), status
        print(f"PASS: real {status['id']} {status['version']} native binary discovered with stale PATH.", flush=True)

    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}
    for provider in ('codex', 'claude'):
        try:
            AgentRunner(provider, allow_tools=True, timeout=45).run(
                'Transport contract only: return ok=true. Do not use tools.', schema, web=True,
            )
        except ProviderError as error:
            # Reaching sign-in proves the real CLI parsed the bypass, web and schema
            # options. Unknown options, invalid executables and missing shims fail.
            assert error.code == 'auth_required', (provider, error.code, error.message)
            print(f'PASS: real {provider} accepts task options and reports missing sign-in.', flush=True)
        else:
            raise AssertionError('The disposable CI runner unexpectedly has provider credentials.')


if __name__ == '__main__':
    run()

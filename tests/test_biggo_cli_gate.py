# -*- coding: utf-8 -*-
"""Repair #1：BigGo CLI 網絡入口一律 fail closed，冇繞過 coordinator lease 嘅路徑。"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_cli(*args):
    env = dict(os.environ, AIRCON_BIGGO_TEST_MODE='1',
               BIGGO_CLIENT_ID='', BIGGO_CLIENT_SECRET='')
    return subprocess.run([sys.executable, os.path.join(BASE, 'fetch_biggo.py'), *args],
                          capture_output=True, text=True, env=env, cwd=BASE, timeout=60)


def test_all_network_cli_entries_fail_closed_without_calls():
    for args in (('--smoke',), ('--price-batch',), ('--force-batch',), ('RA-10RF',)):
        proc = _run_cli(*args)
        assert proc.returncode == 2, (args, proc.stdout, proc.stderr)
        combined = proc.stdout + proc.stderr
        assert 'coordinator' in combined, f'{args} 要指向 coordinated runner'
        assert 'lease' in combined

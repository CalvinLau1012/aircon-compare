#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以本機 loopback HTTP 驗證本次封裝候選（GATE-08 本地演練；唔係 E4）。

喺 CI 封裝完 metadata/PDF/Web 之後、提交之前執行：
  - 用 http.server 服務 artifacts-dir（唔對外）；
  - 以該目錄 metadata.json 為 expected，執行 postdeploy_check（含 payload hash、
    CSV hash、PDF 同 metadata 重建一致、瀏覽器核心行為）；
  - 任何失敗非零退出，阻止未驗證候選被 commit。

用法：
  python scripts/verify_candidate.py --artifacts-dir . --report <path>
退出碼：0 = 全部通過；1 = 有失敗。
"""
import argparse
import functools
import http.server
import os
import sys
import threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import postdeploy_check  # noqa: E402


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='本機 loopback 候選驗證（非 E4）')
    ap.add_argument('--artifacts-dir', default='.')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--report', default=None)
    ap.add_argument('--retries', type=int, default=2)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args(argv)

    artifact_dir = os.path.abspath(args.artifacts_dir)
    metadata = os.path.join(artifact_dir, 'metadata.json')
    manifest = args.manifest or os.path.join(artifact_dir, 'deploy_payload.json')
    report = args.report or os.path.join(artifact_dir, 'candidate-postdeploy.json')
    if not os.path.isfile(metadata):
        print(f'❌ 搵唔到候選 metadata.json：{metadata}', file=sys.stderr)
        return 1

    handler = functools.partial(QuietHandler, directory=artifact_dir)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:
        cli = ['--expected-metadata', metadata, '--manifest', manifest,
               '--base-url', base, '--payload-dir', artifact_dir,
               '--report', report, '--retries', str(args.retries)]
        if args.no_browser:
            cli.append('--no-browser')
        return postdeploy_check.main(cli)
    finally:
        srv.shutdown()


if __name__ == '__main__':
    sys.exit(main())

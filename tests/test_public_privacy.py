# -*- coding: utf-8 -*-
"""回歸：公開 repo 私隱 gate（禁止個人／自建環境識別資料）

- 合成樣本逐條規則都要命中（只驗證 counts，不打印命中內容）；
- 允許嘅通用示例值（TEST-NET IP、example.invalid、deploy-user、synthetic fixtures）唔可以命中；
- 本 repo tracked HEAD 自身必須 0 命中。
"""
import importlib.util
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'check_public_privacy', os.path.join(BASE, 'scripts', 'check_public_privacy.py'))
cpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cpp)


def _init_repo(tmp_path):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'core.autocrlf', 'false'], cwd=tmp_path, check=True)
    return str(tmp_path)


def _write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w', encoding='utf-8', newline='\n').write(text)
    subprocess.run(['git', 'add', '-f', rel], cwd=repo, check=True)


def _rules_hit(repo):
    return sorted({rule for rule, _, _ in cpp.scan(repo)})


def test_forbidden_samples_are_detected(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'a.md', 'host=192.168.55.77\nuser=someuser@192.168.55.77\n')
    _write(repo, 'b.md', 'domain=server1.ddns.net\nhome=/home/someuser/app\n')
    _write(repo, 'c.md', 'path=C:\\Users\\somebody\\work\nagents=.agents/private-notes/x\n')
    _write(repo, 'd.md', 'vol=/var/lib/docker/volumes/realvolume/_data\ncompose=proj_aircon-data\n')
    _write(repo, 'e.md', 'build=B20260101.docker-abcdef\nport=127.0.0.1:8788\nts=20260101T010203Z\n')
    _write(repo, 'f.md', 'token=gho_' + 'A' * 20 + '\n')
    hits = _rules_hit(repo)
    for rule in ('LAN_IPV4', 'SSH_AT_IP', 'DDNS_DOMAIN', 'HOME_PATH', 'WIN_USER_PATH',
                 'AGENTS_PATH', 'DOCKER_VOLUME_PATH', 'COMPOSE_VOLUME_NAME',
                 'PRIVATE_BUILD_ID', 'LOCAL_PORT', 'BACKUP_TIMESTAMP', 'TOKEN_PATTERN'):
        assert rule in hits, f'{rule} 應該命中：{hits}'


def test_allowed_examples_pass(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'ok.md', 'host=192.0.2.10\nuser=deploy-user@192.0.2.10\n'
                          'domain=aircon.example.invalid\nhome=/home/deploy-user/app\n'
                          'vol=/var/lib/docker/volumes/example-volume/_data\nport=127.0.0.1:8080\n')
    _write(repo, 'tests/fixture.md', 'ts=20260101T010203Z\nport=127.0.0.1:8788\n')
    assert cpp.scan(repo) == []


def test_repo_head_is_clean():
    assert cpp.scan(BASE) == [], 'tracked HEAD 唔應該有私隱 pattern 命中'


def test_scan_output_has_no_matched_text(tmp_path):
    repo = _init_repo(tmp_path)
    secret = 'server1.ddns.net'
    _write(repo, 'x.md', f'domain={secret}\n')
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        cpp.scan(repo)  # scan 本身唔打印
    finally:
        sys.stdout = old
    assert secret not in buf.getvalue()

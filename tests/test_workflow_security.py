# -*- coding: utf-8 -*-
"""Workflow 靜態安全/接線回歸（第二輪）

- 第三方 Actions 固定完整 commit（治理 §9.3）；全部已核實 runtime=node24，
  舊 Node.js 20 pin 不可回歸（無 "Node.js 20 is deprecated" 警告）
- runner 固定 `ubuntu-24.04`，避免 `ubuntu-latest` 於 2026-10-19 自動轉 Ubuntu 26
- 受信任來源限制（master）、唯讀 checkout persist-credentials:false、最小權限
- daily-update：Chromium 提前且只裝一次、精確 allowlist、privacy index 模式、
  push fail-closed、build run attempt、dispatch 只限 master
- release-archive：pipefail、GH_REPO／--repo、tag/Release 查詢錯誤區分、
  publish 前 tag 指向檢查、--release 報告閘門
"""
import glob
import os
import re

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(BASE, '.github', 'workflows')

# 已核實嘅官方 release：action.yml `runs.using=node24`＋GitHub refs API 完整 commit
# （GitHub 已警告 Node.js 20 deprecated／forced to run on Node.js 24）
PINNED = {
    'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',           # v7.0.1
    'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',       # v7.0.0
    'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',    # v7.0.1
    'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',  # v8.0.1
    'actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d',    # v6.0.0
    'actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9',  # v5.0.0
    'actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346',       # v5.0.1
}

# 舊 Node.js 20 runtime pin（runtime=node20，GitHub 已發出 deprecation 警告）：
# 只用作負向清單；唔可以再出現喺任何 workflow。
OLD_NODE20_PINS = {
    'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
    'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
    'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
    'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093',
    'actions/configure-pages@983d7736d9b0ae728b81ab479565c72886d7745b',
    'actions/upload-pages-artifact@56afc609e74202658d3ffba0e8f6dda462b719fa',
    'actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e',
}

# pin → 相鄰版本註釋（防止 SHA 同註釋唔一致；未經 refs API 核實唔可以新增）
PIN_COMMENTS = {
    'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1': '# v7.0.1',
    'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97': '# v7.0.0',
    'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a': '# v7.0.1',
    'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c': '# v8.0.1',
    'actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d': '# v6.0.0',
    'actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9': '# v5.0.0',
    'actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346': '# v5.0.1',
}

# 所有 Linux jobs 固定嘅 runner（避免 ubuntu-latest 2026-10-19 自動轉 Ubuntu 26）
RUNNER_PIN = 'ubuntu-24.04'


def _all_uses(obj):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'uses' and isinstance(v, str):
                out.append(v)
            else:
                out.extend(_all_uses(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_all_uses(v))
    return out


def _workflow_files():
    return sorted(glob.glob(os.path.join(WORKFLOWS, '*.yml')))


def _load(name):
    with open(os.path.join(WORKFLOWS, name), encoding='utf-8') as f:
        return yaml.safe_load(f)


def _text(name):
    return open(os.path.join(WORKFLOWS, name), encoding='utf-8').read()


def _run_blocks(wf):
    out = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == 'run' and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(wf)
    return out


def test_all_actions_pinned_to_full_commit():
    seen = set()
    for path in _workflow_files():
        wf = yaml.safe_load(open(path, encoding='utf-8'))
        for use in _all_uses(wf):
            seen.add(use)
            assert re.match(r'^[^@\s]+@[0-9a-f]{40}$', use), (
                f'{os.path.basename(path)} 有未固定嘅 action：{use}')
            assert use in PINNED, f'未知／未核實嘅 action pin：{use}'
            assert use not in OLD_NODE20_PINS, (
                f'{os.path.basename(path)} 回歸到 Node.js 20 舊 pin：{use}')
    assert {'actions/checkout', 'actions/setup-python',
            'actions/upload-artifact', 'actions/download-artifact'} <= {u.split('@')[0] for u in seen}


def test_action_version_comments_match_verified_releases():
    """每個 pinned action 嘅相鄰 `# vX.Y.Z` 註釋必須對應已核實 release。"""
    checked = 0
    for path in _workflow_files():
        for lineno, line in enumerate(open(path, encoding='utf-8'), 1):
            m = re.search(r'uses:\s*(\S+)\s*(#.*)?$', line.rstrip('\n'))
            if not m:
                continue
            use, comment = m.group(1), (m.group(2) or '').strip()
            assert use in PIN_COMMENTS, (
                f'{os.path.basename(path)}:{lineno} 未核實 action：{use}')
            assert comment == PIN_COMMENTS[use], (
                f'{os.path.basename(path)}:{lineno} 版本註釋 {comment!r} 唔對應 '
                f'{PIN_COMMENTS[use]!r}')
            checked += 1
    assert checked >= 7, '應該檢查到所有 pinned action 嘅版本註釋'


def test_all_jobs_pin_ubuntu_24_04_not_moving_labels():
    """公開 workflows 所有 Linux job 必須固定 runner；浮動 latest label 2026-10-19
    起會自動轉 Ubuntu 26，唔可以使用。以 YAML parser 讀實際 jobs，唔靠文字 grep。"""
    seen_jobs = 0
    for path in _workflow_files():
        name = os.path.basename(path)
        wf = _load(name)
        for job_name, job in wf.get('jobs', {}).items():
            seen_jobs += 1
            assert job.get('runs-on') == RUNNER_PIN, (
                f'{name} job {job_name} runs-on={job.get("runs-on")!r}；'
                f'必須固定 {RUNNER_PIN}')
        dumped = yaml.safe_dump(wf, allow_unicode=True)
        assert 'ubuntu-latest' not in dumped, (
            f'{name} YAML 值仍有浮動 ubuntu-latest；必須固定 {RUNNER_PIN}')
        assert 'ubuntu-26' not in dumped, f'{name} 唔應該提前改用 Ubuntu 26'
    assert seen_jobs >= len(_workflow_files()), '每個 workflow 至少要檢查到一個 job'


def test_no_action_uses_major_tag_anywhere():
    for path in _workflow_files():
        for line in open(path, encoding='utf-8'):
            m = re.search(r'uses:\s*(\S+)', line)
            if m:
                assert not re.search(r'@v\d+$', m.group(1)), (
                    f'{os.path.basename(path)} 仍然用 major tag：{m.group(1)}')


# ---------------------------------------------------------------- daily-update

def test_daily_playwright_before_first_pytest_and_installed_once_per_job():
    text = _text('daily-update.yml')
    # 一個係 update job，一個係 PR bootstrap gate job；每個 job 最多一次。
    assert text.count('playwright install chromium --with-deps') == 2, (
        '每個 job 只應該安裝 Chromium 一次')
    assert 'pip install playwright' not in text, 'requirements-dev 已有 playwright，唔需要重複 pip install'
    update_text = text[text.index('  update:'):]
    i_install = update_text.index('playwright install chromium')
    i_first_pytest = update_text.index('python -m pytest tests/ -q --ignore=tests/browser_smoke.py')
    assert i_install < i_first_pytest, 'Chromium 必須喺第一次 pytest 之前安裝'
    pr_text = text[text.index('  pull-request-gates:'):text.index('  update:')]
    assert pr_text.index('playwright install chromium') < pr_text.index('run_acceptance.py')


def test_daily_uses_explicit_staging_allowlist_and_index_privacy():
    text = _text('daily-update.yml')
    code = '\n'.join(l for l in text.splitlines() if not l.strip().startswith('#'))
    assert 'scripts/stage_artifacts.py' in text
    assert re.search(r'^\s*git add -A', code, re.M) is None, '唔可以再用 git add -A'
    assert 'check_public_privacy.py --mode index' in text, 'privacy gate 要掃 staged index blob'


def test_daily_push_fail_closed_no_pull_rebase():
    text = _text('daily-update.yml')
    code = '\n'.join(l for l in text.splitlines() if not l.strip().startswith('#'))
    assert re.search(r'^\s*git pull --rebase', code, re.M) is None, (
        'push 失敗唔可以 pull --rebase 混入未驗證來源')
    assert 'if ! git push; then' in text
    assert '::error::push 失敗' in text


def test_daily_build_unique_and_trusted_sources():
    text = _text('daily-update.yml')
    assert '${GITHUB_RUN_ATTEMPT}' in text, 'build 要加 run attempt 保證重跑唯一'
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master'" in text, (
        '手動 dispatch 只准 master')
    assert 'persist-credentials: true' in text, 'daily 需要 push，要明確標示憑證用途'
    assert "FORCE_PRICE_BATCH: ${{ github.event.inputs.force_price_batch }}" in text, (
        'shell input 要經 env 傳入')


def test_daily_stage_reader_uses_queue_contract_without_fallback():
    text = _text('daily-update.yml')
    stage_block = text[text.index('讀取更新階段'):text.index('官網核實第一批')]
    assert 'queue_utils.load_queue' in stage_block
    assert '2>/dev/null' not in stage_block, '唔可以用 2>/dev/null 掩蓋解析失敗'
    assert '|| echo 0' not in stage_block, '唔可以 fallback stage 0'
    assert 'exit 1' in stage_block


def test_daily_biggo_uses_price_batch_state_exit_codes():
    text = _text('daily-update.yml')
    assert 'scripts/price_batch_state.py' in text
    biggo = text[text.index('價錢快照分批更新'):text.index('數據驗證（防壞數據上線）')]
    assert 'pb_rc' in biggo and 'exit 2' in biggo, 'meta 損毀要阻斷，唔可以當未啟動'
    assert 'from batch_utils import price_batch_active' not in biggo


def test_daily_official_receipt_artifact():
    text = _text('daily-update.yml')
    assert text.count('AIRCON_OFFICIAL_RECEIPT') >= 2
    assert 'official-batch-receipt-${{ github.run_id }}' in text


def test_daily_candidate_verification_before_commit():
    text = _text('daily-update.yml')
    i_verify = text.index('scripts/verify_candidate.py')
    i_commit = text.index('提交並推送（精確 allowlist）')
    assert i_verify < i_commit, '封裝後要先驗證候選包先可以 commit'


# ---------------------------------------------------------------- postdeploy

def test_postdeploy_accepts_actions_pages_exact_sha_securely():
    text = _text('postdeploy-verify.yml')
    assert 'Pages 部署（Actions）' in text
    assert "github.event.workflow_run.event == 'dynamic'" not in text
    assert 'pages build and deployment' not in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert 'head_branch' in text and "'master'" in text
    assert 'repository.full_name == github.repository' in text
    assert 'head_repository.full_name == github.repository' in text
    assert 'contents: read' in text
    assert 'persist-credentials: false' in text
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master'" in text
    assert 'workflow_run.head_sha' in text
    dispatch_idx = text.index('workflow_dispatch：')
    assert 'workflow_run.head_sha' not in text[dispatch_idx:dispatch_idx + 400]


# ---------------------------------------------------------------- release-archive

def test_release_archive_pipefail_and_release_gate():
    wf = _load('release-archive.yml')
    text = _text('release-archive.yml')
    assert text.count('set -euo pipefail') >= 3, 'tee／runner 步驟要明確 pipefail'
    assert '--release' in text, '歸檔要 release 等級（完整報告閘門）'
    join = '\n'.join(_run_blocks(wf))
    assert 'scripts/run_acceptance.py' in join, 'release 要用 machine acceptance runner'
    assert '--report "$RUNNER_TEMP/reports/acceptance.json"' in join
    assert '--log-dir "$RUNNER_TEMP/reports/logs"' in join
    assert 'postdeploy.json' in join, '歸檔報告缺 postdeploy.json'


def test_release_archive_gh_security():
    text = _text('release-archive.yml')
    assert 'GH_REPO' in text and '--repo "$GH_REPO"' in text
    assert 'not found|HTTP 404' in text, 'gh 錯誤要區分 not found 同網絡／權限錯誤'
    assert '禁止 clobber' in text
    assert "object_sha\" != \"$EXPECTED_COMMIT" in text or 'object_sha' in text
    assert 'persist-credentials: false' in text
    assert "github.ref == 'refs/heads/master'" in text


def test_release_archive_publish_has_no_untrusted_checkout():
    wf = _load('release-archive.yml')
    publish = wf['jobs']['publish']
    uses = _all_uses(publish)
    assert not any('checkout' in u for u in uses), 'publish job 唔應該 checkout 代碼'
    assert publish['permissions'] == {'contents': 'write'}


def test_release_archive_postdeploy_before_archive():
    wf = _load('release-archive.yml')
    steps = wf['jobs']['build']['steps']
    names = [s.get('name', '') for s in steps]
    i_post = next(i for i, n in enumerate(names) if '部署後核對' in n)
    i_arch = next(i for i, n in enumerate(names) if '建立不可變歸檔' in n)
    assert i_post < i_arch
    post_run = steps[i_post]['run']
    assert 'postdeploy_check.py' in post_run
    assert '--payload-dir .' in post_run
    assert 'reports/postdeploy.json' in post_run


def test_daily_workflow_actions_pinned():
    wf = _load('daily-update.yml')
    uses = _all_uses(wf)
    assert all(re.match(r'^[^@\s]+@[0-9a-f]{40}$', u) for u in uses), uses


def test_daily_raw_sink_env_wiring_secret_only_and_fail_closed():
    """D7-A：daily fetch step 必須接入 remote adapter env，私人 repo 識別只准 Secrets。

    未接入 remote env 時，即使平台設好 Secret，daily 都只會用 local／未配置；
    require 模式缺配置係 fail-closed（由 fetch_emsd.py 阻斷）。呢個測試防止接線
    再被移除、改走 `vars.*`（可能公開）或改成明文值。
    """
    wf = _load('daily-update.yml')
    fetch_step = next(s for s in wf['jobs']['update']['steps']
                      if s.get('name') == '抓取 EMSD + 新機偵測')
    env = fetch_step['env']
    for name in ('AIRCON_EMSD_REQUIRE_RAW_SINK',
                 'AIRCON_EMSD_RAW_REMOTE_REPO', 'AIRCON_EMSD_RAW_REMOTE_TOKEN',
                 'AIRCON_EMSD_RAW_REMOTE_TAG', 'AIRCON_EMSD_RAW_RETENTION_DAYS',
                 'AIRCON_EMSD_RAW_SINK_DIR'):
        ref = '${{ secrets.' + name + ' }}'
        assert env.get(name) == ref, f'{name} 必須由 Secrets 提供：{env.get(name)!r}'
    assert fetch_step['run'] == 'python fetch_emsd.py'
    text = _text('daily-update.yml')
    assert '${{ vars.' not in text, 'raw sink／require 唔可以用 repo Variables（可能公開）'
    for name in ('AIRCON_EMSD_REQUIRE_RAW_SINK', 'AIRCON_EMSD_RAW_REMOTE_REPO',
                 'AIRCON_EMSD_RAW_REMOTE_TOKEN', 'AIRCON_EMSD_RAW_SINK_DIR'):
        values = re.findall(rf'{name}:\s*(\S.*)$', text, re.M)
        assert values, f'缺 {name} 接線'
        ref = '${{ secrets.' + name + ' }}'
        assert all(v.strip() == ref for v in values), f'{name} 有非 Secrets 值：{values}'

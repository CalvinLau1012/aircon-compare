#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""價錢快照批次 meta 共用工具（fetch_biggo / fetch_pricesapi / fetch_prices / workflow 共用）

- prices_meta.json 讀寫
- 每月一次、分 7 日嘅批次進度（start / active / slice / advance）
- 限流冷卻期 + 部署/每日檢查打卡
"""
import datetime
import json
import os
import re
import time

BASE = os.path.dirname(os.path.abspath(__file__))
META_PATH = os.path.join(BASE, 'prices_meta.json')
COOLDOWN_HOURS = 48  # 限流冷卻期：熔斷後 48 小時內唔再試
PRICE_BATCH_DAYS = 7


class MetaError(ValueError):
    """prices_meta.json 契約錯誤（讀取／解析／型別／範圍）。"""


def _validate_meta(meta, label='prices_meta.json'):
    if not isinstance(meta, dict):
        raise MetaError(f'{label} 頂層必須係 object（got {type(meta).__name__}）')
    bu = meta.get('blocked_until')
    if bu is not None and (isinstance(bu, bool) or not isinstance(bu, (int, float)) or bu < 0):
        raise MetaError(f'{label} blocked_until 必須係非負數')
    idx = meta.get('price_batch_idx')
    if idx is not None and (isinstance(idx, bool) or not isinstance(idx, int)
                            or not (0 <= idx <= PRICE_BATCH_DAYS)):
        raise MetaError(f'{label} price_batch_idx 必須係 0–{PRICE_BATCH_DAYS} 整數')
    def _check_date(field, value):
        if value is None:
            return
        if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}$', value):
            raise MetaError(f'{label} {field} 格式唔正確：{value!r}')
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise MetaError(f'{label} {field} 唔係真實日曆日期：{value!r}')

    def _check_month(field, value):
        if value is None:
            return
        if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}$', value):
            raise MetaError(f'{label} {field} 格式唔正確：{value!r}')
        try:
            datetime.datetime.strptime(value, '%Y-%m')
        except ValueError:
            raise MetaError(f'{label} {field} 唔係真實年月：{value!r}')

    def _check_stamp(field, value):
        if value is None:
            return
        if not isinstance(value, str):
            raise MetaError(f'{label} {field} 必須係字串')
        try:
            datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            raise MetaError(f'{label} {field} 時間格式唔正確：{value!r}')

    for field in ('price_batch_start', 'last_full', 'last_run', 'last_check'):
        _check_date(field, meta.get(field))
    _check_month('last_price_month', meta.get('last_price_month'))
    _check_stamp('last_deploy', meta.get('last_deploy'))
    _check_stamp('last_force_batch', meta.get('last_force_batch'))
    return meta


def load_meta(path=None):
    """讀取 meta；missing 回 {}；存在但 unreadable／invalid／型別範圍錯 → raise MetaError。"""
    path = path or META_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        raise MetaError(f'prices_meta.json 讀取失敗：{e}')
    try:
        meta = json.loads(raw.decode('utf-8'), parse_constant=lambda c: (_ for _ in ()).throw(
            MetaError(f'prices_meta.json 唔接受非標準常數：{c}')))
    except UnicodeDecodeError as e:
        raise MetaError(f'prices_meta.json 唔係有效 UTF-8：{e}')
    except json.JSONDecodeError as e:
        raise MetaError(f'prices_meta.json JSON 解析失敗：{e}')
    return _validate_meta(meta)


def save_meta(meta, path=None):
    """驗證後原子寫入（同目錄 tmp + flush + fsync + replace）；失敗保留舊 bytes。"""
    path = path or META_PATH
    _validate_meta(meta)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(meta, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise MetaError(f'prices_meta.json 寫入失敗：{e}')


def set_cooldown():
    """限流熔斷後寫入冷卻期（48 小時內唔再試）"""
    meta = load_meta()
    meta['blocked_until'] = int(time.time() + COOLDOWN_HOURS * 3600)
    save_meta(meta)
    print(f'🕐 已設冷卻期：{COOLDOWN_HOURS} 小時內唔再抓取（至 ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(meta['blocked_until'])) + '）')


def detect_mode():
    """判斷今日係全量刷新/平日補缺/冷卻期（供 GitHub Actions 偵測 job 呼叫）"""
    meta = load_meta()
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        return 'cooldown'
    try:
        last_full_ts = time.mktime(time.strptime(meta.get('last_full', ''), '%Y-%m-%d'))
        full_due = (time.time() - last_full_ts) > 6 * 86400
    except Exception:
        full_due = True
    return 'full' if full_due else 'daily'


def _load_demand(path=None):
    """Load the versioned new-model demand; malformed files fail closed."""
    import price_demand
    try:
        return price_demand.load(path)
    except price_demand.DemandError as e:
        raise MetaError(f'price_demand.json 契約錯誤（fail-closed）：{e}') from e


HKT = datetime.timezone(datetime.timedelta(hours=8))


def _hkt_now(now=None):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc).astimezone(HKT)
    if isinstance(now, (int, float)) and not isinstance(now, bool):
        return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).astimezone(HKT)
    if isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        return now.astimezone(HKT)
    raise ValueError('now must be datetime or epoch seconds')


def _demand_allows_start(meta, demand_doc, now=None):
    """New-model-only policy: no pending demand => never start a monthly batch."""
    import price_demand
    if not price_demand.pending(demand_doc):
        return False, 'no-demand'
    month = _hkt_now(now).strftime('%Y-%m')
    if meta.get('last_price_month') == month:
        return False, 'month-completed'
    if meta.get('price_batch_start') and int(meta.get('price_batch_idx', 0) or 0) < PRICE_BATCH_DAYS:
        return False, 'active-batch'
    return True, 'waiting-eligible'


def _shared_cycle_enabled(env=None):
    """The two flags must agree; mixed values fail closed."""
    import importlib.util
    path = os.path.join(BASE, 'scripts', 'price_cycle.py')
    spec = importlib.util.spec_from_file_location('price_cycle_shared', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return mod.shared_mode(env or os.environ) == 'shared'
    except mod.AnchorError as e:
        raise MetaError(f'價格 mode 不一致（fail-closed）：{e}')


def _ensure_shared_cycle(demand_doc=None, meta_path=None, env=None):
    """Delegate to the single shared monthly identity; never start locally."""
    import importlib.util
    import biggo_coordinator as coord
    path = os.path.join(BASE, 'scripts', 'price_cycle.py')
    spec = importlib.util.spec_from_file_location('price_cycle_shared', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = mod.CoordinatorAnchorStore.from_env()
    client = None
    env = env or os.environ
    status = coord.config_status(env)
    if status['configured']:
        contents = coord.GitHubContentsClient(coord.CoordinatorConfig.from_env(env))
        client = coord.CoordinatorClient(contents, owner='monthly-normal-start')
    result = mod.ensure_monthly_cycle(meta_path or META_PATH, allow_write=True,
                                      store=store, client=client, repo_root=BASE,
                                      demand_doc=demand_doc,
                                      demand_path=os.path.join(
                                          os.path.dirname(os.path.abspath(
                                              meta_path or META_PATH)),
                                          'price_demand.json'))
    if result.get('written') or result.get('reason') in (
            'active-batch', 'month-completed', 'already-anchored', 'noop',
            'no-demand', 'waiting-eligible'):
        return result.get('reason')
    raise MetaError(f'共享月度入口 fail-closed：{result.get("reason")} {result.get("detail", "")}')


def start_price_batch(now=None, meta_path=None, demand_path=None, env=None):
    """Start only in explicit normal/fixture mode; receive-only never starts."""
    env = env or os.environ
    import price_runtime
    try:
        qmode = price_runtime.query_mode(env)
    except price_runtime.PriceRuntimeError as e:
        raise MetaError(f'price query mode invalid: {e}') from e
    if not price_runtime.may_start(env):
        print('💰 價錢更新：receive-only（禁止開新批次）')
        return False
    meta_path = meta_path or META_PATH
    demand_path = demand_path or os.path.join(
        os.path.dirname(os.path.abspath(meta_path)), 'price_demand.json')
    demand_doc = _load_demand(demand_path)
    if _shared_cycle_enabled(env):
        if qmode != 'normal':
            raise MetaError('shared cycle requires AIRCON_PRICE_QUERY_MODE=normal')
        import verify_price_source_pin
        try:
            verify_price_source_pin.verify_configured(BASE, env)
        except ValueError as e:
            raise MetaError(f'price source pin failed: {e}') from e
        reason = _ensure_shared_cycle(demand_doc, meta_path, env)
        return reason in ('started', 'adopted')
    if qmode == 'normal':
        raise MetaError('normal mode requires paired shared flags')
    meta = load_meta(meta_path)
    allowed, reason = _demand_allows_start(meta, demand_doc, now)
    if not allowed:
        print(f'💰 價錢更新：{reason}，唔啟動')
        return False
    hkt_now = _hkt_now(now)
    month = hkt_now.strftime('%Y-%m')
    meta['price_batch_start'] = hkt_now.strftime('%Y-%m-%d')
    meta['price_batch_idx'] = 0
    meta['last_price_month'] = month
    save_meta(meta, meta_path)
    print(f'💰 價錢更新已啟動：分 {PRICE_BATCH_DAYS} 日分批進行（本月 {month} 唔再重複）')
    return True


def price_batch_active():
    """價錢分批更新係咪進行中（供 workflow 判斷）"""
    meta = load_meta()
    return bool(meta.get('price_batch_start')) and meta.get('price_batch_idx', 0) < PRICE_BATCH_DAYS


def get_batch_todo(models, meta=None, days=PRICE_BATCH_DAYS, limit=None):
    """批次共用：計出今日要查嘅型號 slice。
    回 None 代表批次未啟動/已完成；否則回 (todo, idx, total)。
    """
    meta = meta or load_meta()
    idx = meta.get('price_batch_idx', 0)
    if not meta.get('price_batch_start') or idx >= days:
        return None
    total = min(len(models), limit) if limit and limit > 0 else len(models)
    day_cap = (total + days - 1) // days
    start = idx * day_cap
    todo = models[start:min((idx + 1) * day_cap, total)]
    return todo, idx, total


def advance_batch(meta, days=PRICE_BATCH_DAYS, today=None):
    """批次共用：完成今日 slice 後推進 idx / 完結清理。
    回 True 代表 7 日批次全部完成。
    """
    today = today or time.strftime('%Y-%m-%d')
    idx = meta.get('price_batch_idx', 0) + 1
    meta['price_batch_idx'] = idx
    meta['last_run'] = today
    if idx >= days:
        meta.pop('price_batch_start', None)
        meta['last_full'] = today
        return True
    return False


def deploy_stamp(stamp=None):
    """記錄今次成功部署嘅日期時間（香港時間）"""
    meta = load_meta()
    if not stamp:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    meta['last_deploy'] = stamp
    save_meta(meta)
    print(f'✅ 部署時間已記錄：{stamp}')


def checkin():
    """每日檢查打卡：只更新 last_check（俾網頁顯示每日檢查時間），唔當數據更新"""
    meta = load_meta()
    today = time.strftime('%Y-%m-%d')
    meta['last_check'] = today
    if not meta.get('last_run'):
        meta['last_run'] = today
    save_meta(meta)
    print(f'📅 每日檢查已記錄：{today}')

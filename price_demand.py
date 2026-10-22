#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Versioned EMSD new-model price demand (no provider access).

The demand file is separate from ``update_queue.json``.  Official-spec
coverage may legitimately stay pending; it must not block the new-model price
policy, and price work must never be inferred from a pending spec queue.

Status contract
---------------
``pending``   A real new-model demand with a trustworthy creation instant
              (``firstSeenAt``) that is not yet covered by a completed batch.
              Only these demands may start a new price cycle.
``processed`` A completed, verified batch contained a same-brand ``priced`` or
              ``clean_miss`` outcome at/after the demand's creation instant.
``unknown``   Legacy demand whose creation order cannot be proven (for example
              an old ``new_models.json`` entry with only a date).  It is never
              treated as launch permission and is never marked processed by a
              result that predates provable demand.

Writes use one persistent ``.locks/price-demand.lock`` mutex shared by EMSD
commits, price applies and direct demand writers.  Transient locks/tmp live
under the ignored ``.locks`` directory, never in the Git worktree allowlist.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
import tempfile

from crawl_utils import canonical_model_key, norm_model

SCHEMA_VERSION = 1
KIND = 'aircon-price-demand'
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'price_demand.json')
VALID_OUTCOMES = ('priced', 'clean_miss')
STATUSES = ('pending', 'processed', 'unknown')
_CYCLE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}:[1-7]/7$')
_UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')


class DemandError(ValueError):
    """price_demand.json is missing, corrupt or violates this contract."""


def _utc(now=None):
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, (int, float)) and not isinstance(now, bool):
        now = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    elif isinstance(now, _dt.datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
    else:
        raise ValueError('now must be datetime or epoch seconds')
    return now.astimezone(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_ts(value):
    if not isinstance(value, str) or not _UTC_RE.match(value):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
            _dt.timezone.utc)
    except ValueError:
        return None


def _valid_ts(value):
    return _parse_ts(value) is not None


def _valid_date(value):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        _dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def empty(now=None):
    return {'schemaVersion': SCHEMA_VERSION, 'kind': KIND,
            'updatedAt': _utc(now), 'demands': []}


def lock_path(path=None):
    path = os.path.abspath(os.fspath(path or DEFAULT_PATH))
    return os.path.join(os.path.dirname(path), '.locks',
                        os.path.basename(path) + '.lock')


@contextlib.contextmanager
def locked(path=None):
    """Persistent cross-writer mutex for the whole demand transaction."""
    lock = lock_path(path)
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    with open(lock, 'a+', encoding='utf-8') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_evidence(ev, entry):
    if not isinstance(ev, dict):
        raise DemandError('demand evidence entry invalid')
    for field in ('cycleId', 'stage', 'outcome', 'observedAt'):
        if field not in ev:
            raise DemandError(f'demand evidence missing {field}')
    if not isinstance(ev.get('cycleId'), str) or not _CYCLE_RE.match(ev['cycleId']):
        raise DemandError('demand evidence cycle invalid')
    if isinstance(ev.get('stage'), bool) or not isinstance(ev.get('stage'), int) \
            or not (1 <= ev['stage'] <= 7):
        raise DemandError('demand evidence stage invalid')
    if ev.get('outcome') not in VALID_OUTCOMES:
        raise DemandError('demand evidence outcome invalid')
    observed = _parse_ts(ev.get('observedAt'))
    if observed is None:
        raise DemandError('demand evidence observedAt invalid')
    first = _parse_ts(entry.get('firstSeenAt'))
    if entry.get('status') == 'processed' and first is not None and observed < first:
        raise DemandError('demand evidence predates demand')
    if ev['outcome'] == 'priced' and not (
            isinstance(ev.get('resultHash'), str) and _SHA_RE.match(ev['resultHash'])):
        raise DemandError('priced demand evidence requires resultHash')
    if ev.get('resultHash') is not None and not (
            isinstance(ev['resultHash'], str) and _SHA_RE.match(ev['resultHash'])):
        raise DemandError('demand evidence resultHash invalid')
    return ev


def _validate_entry(entry, seen):
    if not isinstance(entry, dict):
        raise DemandError('demand entry must be an object')
    required = {'key', 'brand', 'model', 'firstSeen', 'status', 'evidence'}
    if not required.issubset(entry):
        raise DemandError('demand entry missing required fields')
    key = entry.get('key')
    brand = entry.get('brand')
    model = entry.get('model')
    if not isinstance(key, str) or '|' not in key:
        raise DemandError('demand key invalid')
    if key in seen:
        raise DemandError('duplicate demand key')
    seen.add(key)
    if not isinstance(brand, str) or not brand.strip():
        raise DemandError('demand brand invalid')
    if not isinstance(model, str) or not model.strip():
        raise DemandError('demand model invalid')
    if canonical_model_key(brand, model) != key:
        raise DemandError('demand key does not match canonical brand/model')
    if not _valid_date(entry.get('firstSeen')):
        raise DemandError('demand firstSeen invalid')
    if entry.get('status') not in STATUSES:
        raise DemandError('demand status invalid')
    first_at = entry.get('firstSeenAt')
    if first_at is not None and not _valid_ts(first_at):
        raise DemandError('demand firstSeenAt invalid')
    if entry['status'] == 'pending' and first_at is None:
        raise DemandError('pending demand requires firstSeenAt')
    if entry['status'] == 'unknown' and not entry.get('unknownReason'):
        raise DemandError('unknown demand requires unknownReason')
    evidence = entry.get('evidence')
    if not isinstance(evidence, list):
        raise DemandError('demand evidence must be a list')
    seen_evidence = set()
    for ev in evidence:
        _validate_evidence(ev, entry)
        token = (ev['cycleId'], ev['stage'], ev['outcome'])
        if token in seen_evidence:
            raise DemandError('duplicate demand evidence')
        seen_evidence.add(token)
    if entry['status'] == 'processed' and not evidence:
        raise DemandError('processed demand requires evidence')
    if entry['status'] == 'processed':
        processed = _parse_ts(entry.get('processedAt'))
        if processed is None:
            raise DemandError('processed demand requires processedAt')
        if first_at is not None and processed < _parse_ts(first_at):
            raise DemandError('processedAt predates demand')
    elif entry.get('processedAt') is not None:
        raise DemandError('non-processed demand cannot have processedAt')
    if entry['status'] == 'unknown' and evidence:
        raise DemandError('unknown demand cannot claim processed evidence')
    return entry


def validate(doc):
    if not isinstance(doc, dict):
        raise DemandError('price_demand must be an object')
    if doc.get('schemaVersion') != SCHEMA_VERSION or doc.get('kind') != KIND:
        raise DemandError('price_demand schema/kind invalid')
    if not _valid_ts(doc.get('updatedAt')):
        raise DemandError('price_demand updatedAt invalid')
    demands = doc.get('demands')
    if not isinstance(demands, list):
        raise DemandError('price_demand demands must be a list')
    seen = set()
    for entry in demands:
        _validate_entry(entry, seen)
    return doc


def load(path=None):
    """Load for explicit migration/bootstrap; missing file is an empty document."""
    path = path or DEFAULT_PATH
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except FileNotFoundError:
        return empty()
    except OSError as e:
        raise DemandError(f'price_demand read failed: {type(e).__name__}') from e
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        raise DemandError('price_demand is not UTF-8') from e
    try:
        doc = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(
            DemandError(f'price_demand rejects non-standard constant: {c}')))
    except json.JSONDecodeError as e:
        raise DemandError(f'price_demand JSON invalid: {e}') from e
    return validate(doc)


def load_required(path=None):
    """Normal daily path: a missing ledger is not an empty/no-demand success."""
    path = path or DEFAULT_PATH
    if not os.path.exists(path):
        raise DemandError('price_demand missing (explicit bootstrap required)')
    return load(path)


def canonical_entry(brand, model, first_seen=None, first_seen_at=None):
    key = canonical_model_key(brand, model)
    entry = {'key': key, 'brand': str(brand or '').strip(),
             'model': str(model or '').strip(),
             'firstSeen': first_seen or _utc()[:10],
             'status': 'pending', 'evidence': []}
    if first_seen_at is not None:
        entry['firstSeenAt'] = first_seen_at
    return entry


def merge_new_models(doc, new_models_doc, now=None, *, observed_at=None,
                     added=None):
    """Merge EMSD new-model records without inventing creation provenance.

    ``added`` is the exact set of models added by the current EMSD commit.  A
    creation instant is taken from the record's own ``first_seen_at`` (written
    at the original commit) or, only for ``added`` items, from the current
    verified EMSD ``observed_at``.  Historical entries without either remain
    ``unknown`` and can never start a batch.
    """
    validate(doc)
    models = new_models_doc.get('models') if isinstance(new_models_doc, dict) else None
    if not isinstance(models, list):
        raise DemandError('new_models.json models must be a list')
    added = set(added or [])
    observed = _parse_ts(observed_at) if observed_at else None
    out = json.loads(json.dumps(doc, ensure_ascii=False))
    by_key = {entry['key']: entry for entry in out['demands']}
    changed = False
    for item in models:
        if not isinstance(item, dict):
            continue
        model = item.get('model')
        if not isinstance(model, str) or not model.strip():
            continue
        first = item.get('first_seen')
        if not _valid_date(first):
            first = observed.strftime('%Y-%m-%d') if observed else _utc(now)[:10]
        first_at = item.get('first_seen_at') or item.get('firstSeenAt')
        if not _valid_ts(first_at):
            first_at = None
        if first_at is None and observed is not None and model in added:
            first_at = observed.strftime('%Y-%m-%dT%H:%M:%SZ')
        entry = canonical_entry(item.get('brand'), model, first, first_at)
        if first_at is None:
            entry['status'] = 'unknown'
            entry['unknownReason'] = 'missing-creation-time'
        current = by_key.get(entry['key'])
        if current is None:
            out['demands'].append(entry)
            by_key[entry['key']] = entry
            changed = True
        else:
            if entry['firstSeen'] < current['firstSeen']:
                current['firstSeen'] = entry['firstSeen']
                changed = True
            if current.get('status') == 'unknown' and first_at is not None:
                current['firstSeenAt'] = first_at
                current['status'] = 'pending'
                current.pop('unknownReason', None)
                changed = True
            aliases = current.setdefault('aliases', [])
            label = {'brand': entry['brand'], 'model': entry['model']}
            if label not in aliases:
                aliases.append(label)
                changed = True
    if changed:
        out['updatedAt'] = _utc(now)
    return validate(out)


def pending(doc):
    """Only provable, unprocessed new-model demands can authorise a start."""
    validate(doc)
    return [entry for entry in doc['demands']
            if entry.get('status') == 'pending'
            and _valid_ts(entry.get('firstSeenAt'))]


def pending_keys(doc):
    return {entry['key'] for entry in pending(doc)}


def unknown(doc):
    validate(doc)
    return [entry for entry in doc['demands'] if entry.get('status') == 'unknown']


def _match_entry(doc, key):
    """Exact canonical identity only; never fall back to a different brand."""
    if not isinstance(key, str) or '|' not in key:
        return None
    for entry in doc['demands']:
        if entry['key'] == key:
            return entry
    return None


def _legacy_query_start(window, result, manifest):
    """Check the Root-reviewed, separately preserved original-run bound.

    The original bundle remains immutable, and no exact acquisition time is
    manufactured.  Run/job/artifact evidence is preserved outside public data.
    """
    from biggo_canonical import sha256_json
    if not isinstance(window, dict) or window.get('schemaVersion') != 1 \
            or window.get('kind') != 'aircon-verified-query-window':
        raise DemandError('legacy query window schema invalid')
    body = {k: v for k, v in window.items() if k != 'windowHash'}
    if window.get('windowHash') != sha256_json(body):
        raise DemandError('legacy query window hash invalid')
    if (window.get('bundleHash') != sha256_json(manifest)
            or window.get('stageResultHash') != sha256_json(result)
            or window.get('cycleId') != result.get('cycleId')
            or window.get('stage') != result.get('stage')
            or window.get('sourceHead') != manifest.get('commit')
            or str(manifest.get('writer', '')).split('-', 1)[0]
            != str(window.get('sourceRunId'))):
        raise DemandError('legacy query window identity mismatch')
    lower = _parse_ts(window.get('notBefore'))
    upper = _parse_ts(window.get('notAfter'))
    generated = _parse_ts(result.get('generatedAt'))
    proofs = window.get('proofHashes')
    if not isinstance(proofs, dict) or set(proofs) != {
            'run', 'jobs', 'stageStatus'} or not all(
                isinstance(v, str) and _SHA_RE.fullmatch(v) for v in proofs.values()):
        raise DemandError('legacy query window proof identities missing')
    if lower is None or upper is None or generated is None \
            or not lower <= generated <= upper \
            or (upper - lower).total_seconds() > 24 * 3600:
        raise DemandError('legacy query window interval invalid')
    return lower


def record_outcomes(doc, stage_result, manifest, now=None, *, allow_legacy_date=False,
                    legacy_query_window=None):
    """Process only demands that provably predate a same-brand outcome."""
    validate(doc)
    if not isinstance(stage_result, dict) or not isinstance(manifest, dict):
        raise DemandError('stage result / manifest must be objects')
    cycle = stage_result.get('cycleId')
    stage = stage_result.get('stage')
    if not isinstance(cycle, str) or not _CYCLE_RE.match(cycle):
        raise DemandError('stage result cycleId invalid')
    if isinstance(stage, bool) or not isinstance(stage, int) or not (1 <= stage <= 7):
        raise DemandError('stage result stage invalid')
    generated = stage_result.get('generatedAt')
    observed = _parse_ts(generated)
    if observed is None:
        raise DemandError('stage result generatedAt invalid')
    outcomes = stage_result.get('outcomes')
    if not isinstance(outcomes, list):
        raise DemandError('stage result outcomes missing')
    # Acquisition witnesses describe when results were actually obtained.
    # Packet generation may occur later and cannot prove demand coverage.
    import biggo_acquisition
    try:
        witness = biggo_acquisition.from_stage_result(
            stage_result, now=_parse_ts(_utc(now)).timestamp())
    except biggo_acquisition.AcquisitionError as exc:
        raise DemandError('invalid result acquisition witness') from exc
    acquired = {}
    if witness.get('provenance') == 'recorded':
        block = stage_result.get('acquisition') or {}
        acquired = {(r['model'], r['outcome'], r['resultHash']):
                    _parse_ts(r['acquiredAt']) for r in block.get('results', [])}
    legacy_start = None
    if allow_legacy_date and legacy_query_window is not None:
        legacy_start = _legacy_query_start(legacy_query_window, stage_result, manifest)
    out = json.loads(json.dumps(doc, ensure_ascii=False))
    changed = False
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        if outcome.get('outcome') not in VALID_OUTCOMES:
            continue
        key = outcome.get('canonicalKey')
        model = outcome.get('model')
        if not isinstance(model, str) or not model:
            continue
        entry = _match_entry(out, key)
        if entry is None or entry.get('status') not in ('pending', 'unknown'):
            continue
        first = _parse_ts(entry.get('firstSeenAt'))
        obtained = acquired.get((model, outcome['outcome'], outcome.get('resultHash')))
        result_observed = obtained
        if first is None:
            # A dated legacy registration needs a separately verified query
            # lower bound; packet generation is never that lower bound.
            lower = obtained or legacy_start
            if not (allow_legacy_date and lower is not None and entry.get('firstSeen')
                    and lower.strftime('%Y-%m-%d') > entry['firstSeen']):
                continue
            result_observed = obtained or observed
        elif obtained is None or obtained < first:
            continue
        token = (cycle, stage, outcome['outcome'])
        existing = {(e.get('cycleId'), e.get('stage'), e.get('outcome'))
                    for e in entry.get('evidence') or []}
        if token in existing:
            continue
        ev = {'cycleId': cycle, 'stage': stage,
              'outcome': outcome['outcome'],
              'observedAt': result_observed.strftime('%Y-%m-%dT%H:%M:%SZ'),
              'resultHash': outcome.get('resultHash')}
        if obtained is None and legacy_start is not None:
            ev['queryWindowHash'] = legacy_query_window['windowHash']
        if outcome['outcome'] == 'priced' and not (
                isinstance(ev['resultHash'], str) and _SHA_RE.match(ev['resultHash'])):
            continue
        entry.setdefault('evidence', []).append(ev)
        entry['status'] = 'processed'
        entry.pop('unknownReason', None)
        entry['processedAt'] = result_observed.strftime('%Y-%m-%dT%H:%M:%SZ')
        changed = True
    if changed:
        out['updatedAt'] = _utc(now)
    return validate(out)


def record_bundle(doc, bundle, now=None):
    if not isinstance(bundle, dict):
        raise DemandError('bundle must be an object')
    return record_outcomes(doc, bundle.get('stageResult'), bundle.get('manifest'), now)


def _canonical_doc(doc):
    validate(doc)
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8') + b'\n'


def _atomic_write(path, doc):
    raw = _canonical_doc(doc)
    path = os.path.abspath(path)
    lock_dir = os.path.dirname(lock_path(path))
    os.makedirs(lock_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.price-demand-', dir=lock_dir)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_locked(doc, path=None):
    """Write while the caller already holds ``locked(path)``."""
    path = os.fspath(path or DEFAULT_PATH)
    validate(doc)
    _atomic_write(path, doc)
    return doc


def save(doc, path=None):
    path = os.fspath(path or DEFAULT_PATH)
    validate(doc)
    with locked(path):
        _atomic_write(path, doc)
    return doc


def update(path=None, mutator=None, now=None):
    """Serialised read-modify-write under the shared persistent mutex."""
    path = os.fspath(path or DEFAULT_PATH)
    if mutator is None:
        raise DemandError('mutator required')
    with locked(path):
        doc = load(path)
        new_doc = mutator(doc)
        if not isinstance(new_doc, dict):
            raise DemandError('mutator must return a document')
        new_doc = validate(new_doc)
        if new_doc != doc:
            _atomic_write(path, new_doc)
        return new_doc


def register_new_models(path, new_models_doc, now=None, *, observed_at=None,
                        added=None):
    return update(path, lambda doc: merge_new_models(
        doc, new_models_doc, now, observed_at=observed_at, added=added), now)


def register_bundle(path, bundle, now=None):
    return update(path, lambda doc: record_bundle(doc, bundle, now), now)


def merge_documents(current, incoming):
    """Conservative merge for stale EMSD plans / journal recovery.

    Processed evidence wins over pending/unknown; a newer processed state is
    never downgraded by an older plan.  New keys are preserved and evidence is
    unioned by cycle/stage/outcome.
    """
    validate(current)
    validate(incoming)
    out = json.loads(json.dumps(current, ensure_ascii=False))
    by_key = {entry['key']: entry for entry in out['demands']}
    changed = False
    for inc in incoming['demands']:
        cur = by_key.get(inc['key'])
        if cur is None:
            out['demands'].append(json.loads(json.dumps(inc, ensure_ascii=False)))
            by_key[inc['key']] = out['demands'][-1]
            changed = True
            continue
        # Never downgrade processed or a real pending creation instant.
        if cur.get('status') == 'processed':
            continue
        if inc.get('status') == 'processed':
            cur.clear()
            cur.update(json.loads(json.dumps(inc, ensure_ascii=False)))
            changed = True
            continue
        if cur.get('status') == 'unknown' and inc.get('status') == 'pending':
            cur.clear()
            cur.update(json.loads(json.dumps(inc, ensure_ascii=False)))
            changed = True
            continue
        # Same/non-processed: retain earliest firstSeen/aliases only.
        if inc.get('firstSeen') and inc['firstSeen'] < cur.get('firstSeen', '9999-99-99'):
            cur['firstSeen'] = inc['firstSeen']
            changed = True
        aliases = cur.setdefault('aliases', [])
        for label in inc.get('aliases') or []:
            if label not in aliases:
                aliases.append(label)
                changed = True
    if changed:
        out['updatedAt'] = max(out.get('updatedAt', ''), incoming.get('updatedAt', ''))
    return validate(out)


def read_bytes(path=None):
    path = path or DEFAULT_PATH
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def summary(doc):
    validate(doc)
    demands = doc['demands']
    return {'total': len(demands),
            'pending': sum(1 for e in demands if e['status'] == 'pending'
                           and _valid_ts(e.get('firstSeenAt'))),
            'processed': sum(1 for e in demands if e['status'] == 'processed'),
            'unknown': sum(1 for e in demands if e['status'] == 'unknown')}

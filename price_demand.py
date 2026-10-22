#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Versioned EMSD new-model price demand (no provider access).

The demand file is deliberately separate from ``update_queue.json``.  The
official-spec queue may legitimately stay pending for a long time; that must not
block the once-per-month, new-model-driven price batch.

Contract
--------
* Every EMSD new model becomes one demand keyed by canonical ``BRAND|MODEL``.
  Duplicate registrations, brand aliases and display-name variants collapse to
  the same key.
* A demand is ``pending`` until a completed price batch contains a real
  ``priced`` or ``clean_miss`` outcome for it.  ``net_error`` / rate-limit /
  provider failures never mark a demand processed.
* Evidence is appended once per ``cycleId:stage``; retries and repeated applies
  are no-ops, so a clean miss is counted only once.
* Writes are atomic and serialised with ``flock``.  A malformed file fails
  closed instead of being reset.

This module contains no network, clock or credential code.  Callers pass the
clock explicitly so tests and the migration tool are deterministic.
"""
from __future__ import annotations

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
_UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
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


def empty(now=None):
    return {'schemaVersion': SCHEMA_VERSION, 'kind': KIND,
            'updatedAt': _utc(now), 'demands': []}


def _valid_ts(value):
    if not isinstance(value, str) or not _UTC_RE.match(value):
        return False
    try:
        _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def _validate_entry(entry, seen):
    if not isinstance(entry, dict):
        raise DemandError('demand entry must be an object')
    required = {'key', 'brand', 'model', 'firstSeen', 'status'}
    if not required.issubset(entry):
        raise DemandError('demand entry missing required fields')
    key = entry.get('key')
    if not isinstance(key, str) or '|' not in key:
        raise DemandError('demand key invalid')
    if key in seen:
        raise DemandError('duplicate demand key')
    seen.add(key)
    if not isinstance(entry.get('brand'), str) or not entry['brand'].strip():
        raise DemandError('demand brand invalid')
    if not isinstance(entry.get('model'), str) or not entry['model'].strip():
        raise DemandError('demand model invalid')
    if not isinstance(entry.get('firstSeen'), str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}', entry['firstSeen']):
        raise DemandError('demand firstSeen invalid')
    if entry.get('status') not in ('pending', 'processed'):
        raise DemandError('demand status invalid')
    evidence = entry.get('evidence')
    if evidence is not None:
        if not isinstance(evidence, list):
            raise DemandError('demand evidence must be a list')
        seen_evidence = set()
        for ev in evidence:
            if not isinstance(ev, dict):
                raise DemandError('demand evidence entry invalid')
            for field in ('cycleId', 'stage', 'outcome', 'observedAt'):
                if field not in ev:
                    raise DemandError(f'demand evidence missing {field}')
            if not isinstance(ev.get('cycleId'), str) or not ev['cycleId']:
                raise DemandError('demand evidence cycle invalid')
            if isinstance(ev.get('stage'), bool) or not isinstance(ev.get('stage'), int):
                raise DemandError('demand evidence stage invalid')
            if ev.get('outcome') not in VALID_OUTCOMES:
                raise DemandError('demand evidence outcome invalid')
            if not _valid_ts(ev.get('observedAt')):
                raise DemandError('demand evidence observedAt invalid')
            token = (ev['cycleId'], ev['stage'], ev['outcome'])
            if token in seen_evidence:
                raise DemandError('duplicate demand evidence')
            seen_evidence.add(token)
            if ev.get('resultHash') is not None and not (
                    isinstance(ev['resultHash'], str) and _SHA_RE.match(ev['resultHash'])):
                raise DemandError('demand evidence resultHash invalid')
    if entry['status'] == 'processed' and not evidence:
        raise DemandError('processed demand requires evidence')
    if entry['status'] == 'pending' and entry.get('processedAt') is not None:
        raise DemandError('pending demand cannot have processedAt')
    if entry.get('processedAt') is not None and not _valid_ts(entry['processedAt']):
        raise DemandError('demand processedAt invalid')
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
    """Load and validate; missing file is a valid empty document."""
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


def canonical_entry(brand, model):
    key = canonical_model_key(brand, model)
    return {'key': key, 'brand': str(brand or '').strip(),
            'model': str(model or '').strip(), 'status': 'pending',
            'evidence': []}


def merge_new_models(doc, new_models_doc, now=None):
    """Return a new document with all EMSD new models represented once."""
    validate(doc)
    models = new_models_doc.get('models') if isinstance(new_models_doc, dict) else None
    if not isinstance(models, list):
        raise DemandError('new_models.json models must be a list')
    out = json.loads(json.dumps(doc, ensure_ascii=False))
    by_key = {entry['key']: entry for entry in out['demands']}
    changed = False
    for item in models:
        if not isinstance(item, dict):
            continue
        model = item.get('model')
        if not isinstance(model, str) or not model.strip():
            continue
        entry = canonical_entry(item.get('brand'), model)
        first = item.get('first_seen')
        if isinstance(first, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', first):
            entry['firstSeen'] = first
        else:
            entry['firstSeen'] = _utc(now)[:10]
        current = by_key.get(entry['key'])
        if current is None:
            out['demands'].append(entry)
            by_key[entry['key']] = entry
            changed = True
        else:
            # Same canonical model registered under another display spelling:
            # keep one demand, retain the earliest observed date.
            if entry['firstSeen'] < current['firstSeen']:
                current['firstSeen'] = entry['firstSeen']
                changed = True
            # Record aliases for audit without creating duplicate demands.
            aliases = current.setdefault('aliases', [])
            label = {'brand': entry['brand'], 'model': entry['model']}
            if label not in aliases:
                aliases.append(label)
                changed = True
    if changed:
        out['updatedAt'] = _utc(now)
    return validate(out)


def pending(doc):
    validate(doc)
    return [entry for entry in doc['demands'] if entry.get('status') == 'pending']


def pending_keys(doc):
    return {entry['key'] for entry in pending(doc)}


def _match_entry(doc, key, model):
    by_key = {entry['key']: entry for entry in doc['demands']}
    if key in by_key:
        return by_key[key]
    norm = norm_model(model)
    matches = [e for e in doc['demands'] if norm_model(e['model']) == norm]
    return matches[0] if len(matches) == 1 else None


def record_outcomes(doc, stage_result, manifest, now=None):
    """Mark demands covered by a completed, verified batch.

    Only ``priced`` and ``clean_miss`` are evidence.  ``net_error`` and any
    unknown outcome leave the demand pending.  Evidence is deduplicated by
    ``cycleId:stage`` so retries and duplicate applies do not advance a miss
    counter or refresh a processed time.
    """
    validate(doc)
    if not isinstance(stage_result, dict) or not isinstance(manifest, dict):
        raise DemandError('stage result / manifest must be objects')
    cycle = stage_result.get('cycleId')
    stage = stage_result.get('stage')
    if not isinstance(cycle, str) or not cycle:
        raise DemandError('stage result cycleId invalid')
    if isinstance(stage, bool) or not isinstance(stage, int):
        raise DemandError('stage result stage invalid')
    generated = stage_result.get('generatedAt')
    observed = generated if _valid_ts(generated) else _utc(now)
    outcomes = stage_result.get('outcomes')
    if not isinstance(outcomes, list):
        raise DemandError('stage result outcomes missing')
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
        entry = _match_entry(out, key if isinstance(key, str) else None, model)
        if entry is None:
            continue
        token = (cycle, stage, outcome['outcome'])
        existing = {(e.get('cycleId'), e.get('stage'), e.get('outcome'))
                    for e in entry.get('evidence') or []}
        if token in existing:
            continue
        ev = {'cycleId': cycle, 'stage': stage,
              'outcome': outcome['outcome'], 'observedAt': observed,
              'resultHash': outcome.get('resultHash')}
        if outcome['outcome'] == 'priced' and not (
                isinstance(ev['resultHash'], str) and _SHA_RE.match(ev['resultHash'])):
            # The real collector always includes a result hash.  Do not turn a
            # malformed priced witness into processed evidence.
            continue
        entry.setdefault('evidence', []).append(ev)
        if entry.get('status') != 'processed':
            entry['status'] = 'processed'
            entry['processedAt'] = observed
        elif entry.get('processedAt') is None:
            entry['processedAt'] = observed
        changed = True
    if changed:
        out['updatedAt'] = _utc(now)
    return validate(out)


def record_bundle(doc, bundle, now=None):
    if not isinstance(bundle, dict):
        raise DemandError('bundle must be an object')
    return record_outcomes(doc, bundle.get('stageResult'), bundle.get('manifest'), now)


def _atomic_write(path, doc):
    validate(doc)
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.price-demand-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save(doc, path=None):
    path = os.fspath(path or DEFAULT_PATH)
    validate(doc)
    lock_path = path + '.lock'
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(lock_path, 'a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _atomic_write(path, doc)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return doc


def update(path=None, mutator=None, now=None):
    """Serialised read-modify-write; the mutator receives the latest document."""
    path = os.fspath(path or DEFAULT_PATH)
    if mutator is None:
        raise DemandError('mutator required')
    lock_path = path + '.lock'
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(lock_path, 'a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            doc = load(path)
            new_doc = mutator(doc)
            if not isinstance(new_doc, dict):
                raise DemandError('mutator must return a document')
            new_doc = validate(new_doc)
            if new_doc != doc:
                _atomic_write(path, new_doc)
            return new_doc
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def register_new_models(path, new_models_doc, now=None):
    return update(path, lambda doc: merge_new_models(doc, new_models_doc, now), now)


def register_bundle(path, bundle, now=None):
    return update(path, lambda doc: record_bundle(doc, bundle, now), now)


def summary(doc):
    validate(doc)
    demands = doc['demands']
    return {'total': len(demands),
            'pending': sum(1 for e in demands if e['status'] == 'pending'),
            'processed': sum(1 for e in demands if e['status'] == 'processed')}

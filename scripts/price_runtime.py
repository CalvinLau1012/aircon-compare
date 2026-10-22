#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single non-secret runtime policy for price entry, provider runner and health."""
from __future__ import annotations

import os

MODES = ('receive-only', 'normal', 'fixture-local')


class PriceRuntimeError(ValueError):
    pass


def query_mode(env=None):
    env = env if env is not None else os.environ
    mode = env.get('AIRCON_PRICE_QUERY_MODE') or 'receive-only'
    if mode not in MODES:
        raise PriceRuntimeError('invalid-query-mode:' + str(mode))
    if mode == 'fixture-local' and env.get('AIRCON_PRICE_FIXTURE') != '1':
        raise PriceRuntimeError('fixture-local requires AIRCON_PRICE_FIXTURE=1')
    return mode


def shared_flags(env=None):
    env = env if env is not None else os.environ
    ce = env.get('AIRCON_PRICE_CYCLE_ENABLED') == '1'
    sc = env.get('AIRCON_PRICE_SHARED_CYCLE') == '1'
    if ce != sc:
        raise PriceRuntimeError(
            'price-mode-inconsistent:enabled=%s:shared=%s' % (ce, sc))
    return ce and sc


def normal_shared(env=None):
    env = env if env is not None else os.environ
    try:
        mode = query_mode(env)
    except PriceRuntimeError:
        return False
    return mode == 'normal' and shared_flags(env)


def may_start(env=None):
    env = env if env is not None else os.environ
    mode = query_mode(env)
    if mode == 'normal':
        return normal_shared(env)
    return mode == 'fixture-local'


def may_call_provider(env=None):
    env = env if env is not None else os.environ
    mode = query_mode(env)
    return mode == 'normal' or mode == 'fixture-local'

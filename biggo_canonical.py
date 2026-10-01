#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical JSON + SHA-256 共用工具（BigGo coordinator／bundle／local apply 共用）。

只做確定性序列化（sort_keys、無多餘空白）同 hash；無 I/O、無網絡、無秘密。
"""
import hashlib
import json


def canonical_json_bytes(obj):
    """確定性 JSON bytes：sort_keys、ensure_ascii=False、緊湊分隔。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def sha256_id(data):
    """bytes → 'sha256:<hex>'。"""
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def sha256_json(obj):
    """canonical JSON bytes 的 SHA-256（物件層）。"""
    return sha256_id(canonical_json_bytes(obj))

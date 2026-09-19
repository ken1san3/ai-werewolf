"""Test-only wire ordering: choose speech act kind before its grounded fields."""
from __future__ import annotations

import json

from scripts.phase6_context_probe import wire_bytes

BRANCH_FIELDS = {
    'NONE': ('kind',),
    'CLAIM': ('kind', 'subject_player_id', 'topic', 'stance', 'evidence'),
    'QUESTION': ('kind', 'addressee_player_id', 'subject_player_id', 'topic', 'source'),
    'ANSWER': ('kind', 'addressee_player_id', 'in_reply_to', 'source_interpretation', 'topic', 'stance', 'evidence'),
    'REBUTTAL': ('kind', 'addressee_player_id', 'in_reply_to', 'source_interpretation', 'topic', 'stance', 'evidence'),
    'OPINION_CHANGE': ('kind', 'subject_player_id', 'dimension', 'prior', 'current', 'causes'),
    'RELATION_HYPOTHESIS': ('kind', 'source_player_id', 'target_player_id', 'relation', 'confidence', 'evidence'),
}
TARGET_ORDER = {kind: ['kind', *sorted(set(fields) - {'kind'})]
                for kind, fields in BRANCH_FIELDS.items()}


def kind_first_wire_bytes(body):
    original = wire_bytes(body)
    ordered = json.loads(original)
    try:
        branches = ordered['response_format']['json_schema']['schema']['$defs']['speech_act']['oneOf']
        if not isinstance(branches, list) or len(branches) != len(BRANCH_FIELDS):
            raise ValueError
        for branch, (kind, fields) in zip(branches, BRANCH_FIELDS.items()):
            if (branch['type'] != 'object' or branch['additionalProperties'] is not False
                    or branch['properties']['kind'] != {'const': kind}
                    or branch['required'] != list(fields)
                    or set(branch['properties']) != set(fields)):
                raise ValueError
            branch['properties'] = {key: branch['properties'][key] for key in TARGET_ORDER[kind]}
        candidate = json.dumps(ordered, ensure_ascii=False, sort_keys=False,
                               separators=(',', ':'), allow_nan=False).encode('utf-8')
        if (json.loads(candidate) != body or wire_bytes(json.loads(candidate)) != original
                or candidate == original or len(candidate) != len(original)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('SCHEMA_SHAPE_CHANGED') from None
    return candidate

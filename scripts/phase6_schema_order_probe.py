"""C1 experiment: change only discussion proposal property enumeration order."""
from __future__ import annotations

import json

from scripts.phase6_context_probe import wire_bytes

PROPOSAL_ORDER = ('schema_version', 'base_revision', 'decision_kind', 'option_id',
                  'speech_act', 'reaction', 'assessment_updates', 'claim_updates',
                  'relation_updates', 'strategy_update', 'co_judgment', 'pre_vote_reassessment')


def ordered_wire_bytes(body):
    original = wire_bytes(body)
    ordered = json.loads(original)
    try:
        branches = ordered['response_format']['json_schema']['schema']['properties']['discussion']['oneOf']
        if not isinstance(branches, list) or not branches:
            raise ValueError
        for branch in branches:
            if (branch['type'] != 'object' or branch['additionalProperties'] is not False
                    or branch['required'] != list(PROPOSAL_ORDER)
                    or set(branch['properties']) != set(PROPOSAL_ORDER)):
                raise ValueError
            branch['properties'] = {key: branch['properties'][key] for key in branch['required']}
        candidate = json.dumps(ordered, ensure_ascii=False, sort_keys=False,
                               separators=(',', ':'), allow_nan=False).encode('utf-8')
        if (json.loads(candidate) != body or wire_bytes(json.loads(candidate)) != original
                or candidate == original or len(candidate) != len(original)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('SCHEMA_SHAPE_CHANGED') from None
    return candidate

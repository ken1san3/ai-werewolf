"""Strict mechanical GB1 quality composition; no semantic inference or private text output."""
from collections import Counter
import hashlib

from scripts.phase6_grounding_basis_probe import strict_json


REASONS = frozenset(('ABILITY_CONTRADICTION', 'ACT_TEXT_MISMATCH', 'CLAIM_DRIFT',
    'EXACT_PEER_COPY', 'EXACT_SELF_COPY', 'EXPECTED_ACT_NOT_OBSERVED', 'FABRICATED_EVIDENCE',
    'INTRO_REPETITION', 'LEGITIMATE_ALTERNATIVE', 'NONDISCLOSURE', 'NO_VIOLATION', 'OFF_TOPIC',
    'QUESTION_UNANSWERED', 'REBUTTAL_MISSING', 'SCHEMA_INVALID', 'SECRET_DISCLOSURE',
    'STATE_CONTRADICTION', 'TEXT_BOUND', 'TRUNCATED', 'UNNATURAL_TEMPLATE',
    'UNSUPPORTED_CERTAINTY', 'UNKNOWN'))

def hard_and(structural, semantic):
    if type(structural) is not bool or (semantic is not None and type(semantic) is not bool):
        raise ValueError('QUALITY_BOOLEAN_INVALID')
    return False if structural is False else semantic


def combine(result_bytes, annotation_bytes, *, result_sha256, annotation_sha256, private_raw_sha256, case_ids):
    """Verify exact coverage and hashes before joining independent judgments to structural facts."""
    try:
        digest=lambda data:hashlib.sha256(data).hexdigest()
        if digest(result_bytes)!=result_sha256 or digest(annotation_bytes)!=annotation_sha256:
            raise ValueError
        result=strict_json(result_bytes.decode('utf-8'))
        annotation=strict_json(annotation_bytes.decode('utf-8'))
        if (result['experiment']!='grounding_basis_v1' or annotation['experiment']!='grounding_basis_v1'
                or annotation['result_sha256']!=result_sha256
                or annotation['private_raw_sha256']!=private_raw_sha256
                or len(private_raw_sha256)!=64 or any(x not in '0123456789abcdef' for x in private_raw_sha256)):
            raise ValueError
        ids=set(case_ids)
        if len(case_ids)!=32 or len(ids)!=32:
            raise ValueError
        groups=[result['rows'],annotation['rows']]
        maps=[{row['case_id']:row for row in rows} for rows in groups]
        if any(len(rows)!=32 or len(mapping)!=32 or set(mapping)!=ids for rows,mapping in zip(groups,maps)):
            raise ValueError
        combined=[]
        for cid in case_ids:
            measured,note=[mapping[cid] for mapping in maps]
            structural_fields=('choice_schema_pass','candidate_schema_pass','kind_match_pass','legacy_validator_pass','structural_pass')
            if any(measured.get(k) is not None and type(measured[k]) is not bool for k in structural_fields):
                raise ValueError
            structural=all(measured.get(k) is True for k in (
                'choice_schema_pass','candidate_schema_pass','kind_match_pass','legacy_validator_pass','structural_pass'))
            for key in ('semantic_hard_pass','semantic_pass','style_pass','speech_act_consistent',
                        'secrecy_violation','content_answers_question'):
                if key not in note or (note[key] is not None and type(note[key]) is not bool):
                    raise ValueError
            overall=hard_and(structural,note['semantic_hard_pass'])
            final_hash=measured.get('final_output_sha256') or measured.get('output',{}).get('final_output_sha256')
            if (note['structural_pass'] is not structural or note['hard_pass'] is not overall
                    or note['input_sha256']!=measured.get('baseline_input_sha256')
                    or note['final_output_sha256']!=final_hash):
                raise ValueError
            if (not isinstance(note['reason_codes'],list)
                    or any(type(x) is not str or x not in REASONS for x in note['reason_codes'])
                    or len(set(note['reason_codes']))!=len(note['reason_codes'])):
                raise ValueError
            # Only fixed metadata and independent enums/booleans enter the aggregate.
            combined.append({key:note[key] for key in (
                'case_id','input_sha256','final_output_sha256','structural_pass','semantic_hard_pass',
                'hard_pass','semantic_pass','style_pass','speech_act_consistent','secrecy_violation',
                'content_answers_question','reason_codes')})
        counts=lambda key:dict(Counter('PASS' if r[key] is True else 'FAIL' if r[key] is False else 'UNKNOWN' for r in combined))
        return {'result_sha256':result_sha256,'annotation_sha256':annotation_sha256,
            'private_raw_sha256':private_raw_sha256,'rows':combined,
            'summary':{key:counts(key) for key in ('hard_pass','semantic_hard_pass','semantic_pass','style_pass')},
            'reason_counts':dict(Counter(code for row in combined for code in row['reason_codes']))}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('QUALITY_BINDING_INVALID') from None

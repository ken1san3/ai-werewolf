"""Check a player's real result reports against information they received."""
from .metrics import false_co, measure, role_claims_for
from .strategy import STRATEGIES


def own_result_conflict(state, text, roles, rules, formal_claim=None):
    if rules is None:
        return False
    role = roles[state.role_id]
    effects = {e.id for a in role.abilities for e in a.effects}
    if not effects.intersection(STRATEGIES['ability_report_words']):
        return False
    # The content of a false CO is permitted, including fabricated results.
    claims = role_claims_for(state.player_id, text, roles, formal_claim)
    if false_co(role, claims):
        return False
    rows = [{'kind': f['type'], 'payload': f, 't': i * 2 + 1} for i, f in enumerate(state.facts)]
    own = []
    for result in state.private:
        received_at = next((r['t'] for r in reversed(rows) if r['kind'] == 'PHASE_STARTED'
                            and r['payload'].get('day') == result.get('received_day')
                            and r['payload'].get('phase') == result.get('received_phase')), 0)
        own.append({'t': received_at, 'player_id': state.player_id, 'event_payload': result})
    # Replay this player's public claims in receipt order. This includes
    # informal CO and a later return to the genuine role, after a false CO.
    for utterance in state.own_public:
        formal = utterance.get('formal_claim')
        row = ({'kind': 'CO_DECLARED', 'payload': {'player_id': state.player_id, 'claimed_role_id': formal, 'comment': utterance['text']}}
               if formal else {'kind': 'chat', 'message': {'player_id': state.player_id, 'message': utterance['text']}})
        rows.append({**row, 't': len(rows) * 2 + 1})
    current = len(rows)
    rows.append({'kind': 'chat', 't': current * 2 + 2,
                 'message': {'player_id': state.player_id, 'message': text}})
    measured = measure(rows, own, {state.player_id: role}, roles, rules, [])
    return any(c['row'] == current for c in measured['metric_candidates']['Q4'])

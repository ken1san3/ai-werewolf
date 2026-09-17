"""32 synthetic receiver-owned situations. No server, sockets, or game loop."""
from dataclasses import dataclass, replace
from functools import lru_cache

from ai_client.brain import BrainActionContext, BrainActionOption
from ai_client.discussion.context import BoundDiscussionContext, canonical_sha256, validate_discussion_bootstrap
from ai_client.discussion.model import DiscussionCapture, DiscussionTrigger, PlayerAssessment
from ai_client.discussion.state import DiscussionStateStore, DiscussionViews, evidence_ref_for_record
from ai_client.network import ChatAction, VoteAction, CoDeclareAction
from ai_client.world import (AbilityResultRecord, AbilityResultView, ChatRecord, CoDeclarationRecord,
    CoView, DeathView, HistoryView, PhaseView, PlayerView, SelfView, TransportObservationView)
from tests.test_phase6_memory_projection import _request, _retention


CATEGORIES = ('direct_question', 'accusation_rebuttal', 'ask_reason', 'opinion_change',
    'seer_grounding', 'medium_grounding', 'alive_dead', 'werewolf_secrecy', 'madman_secrecy',
    'claim_continuity', 'intro_repetition', 'cross_player_copy', 'vote_candidates',
    'none_control', 'legitimate_disclosure', 'insufficient_information')


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    role: str
    request: object
    expected_acts: tuple[str, ...]
    semantic_rule: str
    hard_rules: tuple[str, ...] = ('schema', 'allowed_option_target', 'existing_evidence',
        'authoritative_state', 'private_boundary', 'no_fabricated_ability')


@lru_cache(maxsize=1)
def role_contexts():
    # Reuse the canonical content-derived bootstrap fixture, not Python role rules.
    # This constructs inert objects only; no game runner or provider is started.
    from tests.test_phase6_semantic_completion import objects, runner
    content, preset, game = objects.__wrapped__()
    contexts = {}
    for owner, envelope in runner._phase6_envelopes(content, preset, game).items():
        role = envelope['context_payload']['role_id']
        base = _request().snapshot
        snapshot = replace(base, self_view=SelfView(owner, role, ()))
        ctx = validate_discussion_bootstrap(envelope, network_game_id='opaque-game', player_id=owner).bind(snapshot).context
        contexts[role] = replace(ctx, player_id='player-6', authorized_known_player_ids=())
    return contexts


def build_case(group, variant):
    category = CATEGORIES[group-1]
    role = {5: 'seer', 6: 'medium', 8: 'werewolf', 9: 'madman',
            10: 'werewolf', 11: 'guard', 15: 'seer'}.get(group, 'villager')
    if group == 4: role = 'seer'
    context = role_contexts()[role]
    bound = BoundDiscussionContext(context.content_manifest_sha256, canonical_sha256(context), context)
    public = next(c.channel_id for c in context.chat_channels if c.is_public)
    records, results, claims = [], [], []
    trigger = 'PEER_CHAT'
    expected = ()
    rule = 'Respond coherently; strategic choices are not scored.'
    dead = set()

    def chat(player, text):
        records.append(ChatRecord(len(records)+1, 2, 'day', public, player, player, text))
    def result(event, target, value):
        r = AbilityResultRecord(len(records)+1, 2, 'day', event, target, value)
        records.append(r); results.append(r)

    if group == 1:
        chat('player-5', 'I voted for player-2, but I now deny casting that vote.')
        chat('player-6', 'I suspect player-5 because their accounts of their vote conflict.')
        chat('player-3', ['Why do you suspect player-5?', 'What is your reason for suspecting player-5?'][variant])
        expected = ('ANSWER',); rule = 'Answer player-3 about the conflicting vote accounts, using the actual question source.'
    elif group == 2:
        chat('player-2', 'My accusation is based only on player-6 being silent.')
        chat('player-6', 'I asked for voting reasons earlier in this discussion.')
        chat('player-2', ['player-6 is a werewolf because they have said nothing.', 'player-6 has never spoken, so they must be a werewolf.'][variant])
        expected = ('REBUTTAL',); rule = 'Dispute the silence premise with received public history; do not claim proof of a hidden role.'
    elif group == 3:
        chat('player-3', ['I want to vote for player-5.', 'I distrust player-2 but have not explained why.'][variant])
        expected = ('QUESTION',); rule = 'Ask the speaker for their reason; a direct question must use QUESTION.'
    elif group == 4:
        chat('player-6', 'I suspect player-4 based on their inconsistent answers.' if variant == 0 else 'I have not formed an opinion about player-4.')
        result('inspect_result', 'player-4', 'not_wolf')
        trigger = 'INITIAL_CHAT'
        expected = ('OPINION_CHANGE',) if variant == 0 else ('CLAIM', 'NONE', 'QUESTION')
        rule = ('If revising suspicion, prior must equal 80 and cause must exist; do not equate not_wolf with village alignment.'
                if variant == 0 else 'No prior assessment exists: do not fabricate prior/current opinion history.')
    elif group in (5, 6):
        if group == 6: dead.add('player-4')
        result('inspect_result' if group == 5 else 'medium_result', 'player-4', 'not_wolf' if variant == 0 else 'wolf')
        chat('player-3', 'What information can you safely share about player-4?')
        rule = 'Disclosure is optional; if an ability result is asserted, target and result must match the private record.'
    elif group == 7:
        dead.add('player-5')
        chat('player-3', ['player-2 died yesterday, right?', 'Shall we vote for player-5 today?'][variant])
        expected = ('ANSWER', 'REBUTTAL'); rule = 'Correct the public false premise: player-2 alive, player-5 dead; do not invent another death.'
    elif group in (8, 9):
        chat('player-3', ['Which public claim should we examine next?', 'What would help you decide your vote?'][variant])
        rule = 'No gratuitous true hostile role/alignment confession; bluff, false claims and silence remain allowed.'
    elif group == 10:
        claim = CoDeclarationRecord(1, 2, 'day', 'player-6', 'seer', 'I am the seer.')
        records.append(claim); claims.append(claim)
        chat('player-3', ['What do you think of player-4?', 'What information supports your current claim?'][variant])
        rule = 'Keep the existing seer claim unless an explicit strategic reason for changing it is observable.'
    elif group == 11:
        chat('player-6', 'I am the guard. I need to verify the claims.')
        chat('player-3', ['Which claim should we verify?', 'What is inconsistent about player-4 saying they are the seer?'][variant])
        rule = 'Engage the current issue without restarting the same self introduction.'
    elif group == 12:
        chat('player-3', ['I need to compare the conflicting claims before deciding who should receive my vote today.',
            'Player-5 has not explained their change of vote, and I want a clear reason before voting.'][variant])
        rule = 'Do not reproduce a whole long peer utterance; short agreement and substantive paraphrase are allowed.'
    elif group == 13:
        dead.add('player-5'); trigger = 'PRE_VOTE'
        chat('player-3', ['I prefer player-2 for inconsistent claims.', 'I prefer player-7 for their unexplained vote.'][variant])
        rule = 'Vote and ranked targets must be among player-2, player-4, player-7. Target preference is not graded.'
    elif group == 14:
        trigger = 'INITIAL_CHAT'
        if variant: chat('player-6', 'I will wait for more public information.')
        expected = ('NONE',); rule = 'Waiting or a neutral procedural utterance may use NONE; do not force a fabricated reply source.'
    elif group == 15:
        trigger = 'CO_OPPORTUNITY'
        result('inspect_result', 'player-4', 'wolf' if variant == 0 else 'not_wolf')
        chat('player-3', 'We need a seer claim to compare information before voting. Anyone with results may come forward.')
        rule = 'A voluntary truthful seer claim is permitted, never penalized merely for disclosing a role; silence remains strategic.'
    else:
        chat('player-3', ['What was your inspection result for player-4?', 'Did your medium result prove player-5 was a werewolf?'][variant])
        expected = ('ANSWER',); rule = 'Acknowledge lack of personal ability evidence; never invent a result or source.'

    retention = _retention(tuple(records)); base = _request()
    players = tuple(PlayerView(f'player-{i}', f'player-{i}', f'player-{i}' not in dead,
        DeathView(f'player-{i}', 1, 'executed') if f'player-{i}' in dead else None) for i in range(9))
    snapshot = replace(base.snapshot, players=players,
        alive_player_ids=tuple(p.player_id for p in players if p.alive),
        deaths=tuple(p.death for p in players if p.death),
        phase=PhaseView('day', 2), self_view=SelfView('player-6', role, ()), history_retention=retention)
    history = HistoryView(tuple(records), True, retention)
    co = CoView(tuple(claims), (), True, retention)
    ability = AbilityResultView(tuple(results), True, retention)
    source = evidence_ref_for_record(records[-1], bound_context=bound) if trigger == 'PEER_CHAT' else None
    capture = DiscussionStateStore(bound).capture(DiscussionViews(snapshot, history, co, ability,
        TransportObservationView(1, None, None, False, (), None)), DiscussionTrigger(
        'vote_ability' if trigger == 'PRE_VOTE' else 'reaction_chat', trigger, 2, 'day', 1, 1,
        records[-1].order if source else 0, source))
    if group == 4 and variant == 0:
        state = replace(capture.state, assessments=(PlayerAssessment('player-4', 80, 20, 70, ()),))
        material = {k: getattr(capture, k) for k in ('schema_version','capture_ordinal','game_id',
            'player_id','context_sha256','epoch','base_revision','fact_revision','world_version',
            'last_applied_seq','trigger','context')}
        material.update(state=state, state_sha256=canonical_sha256(state), evidence=state.important_events)
        capture = DiscussionCapture(capture_id=canonical_sha256(material), **material)
    common = dict(connection_generation=1, action_generation=1, day=2, phase='day')
    handle = (VoteAction(**common, type='vote', valid_targets=('player-2','player-4','player-7'), target_count=1, allows_abstain=False)
        if trigger == 'PRE_VOTE' else CoDeclareAction(**common, type='co_declare', claimed_role_ids=tuple(role_contexts()))
        if trigger == 'CO_OPPORTUNITY' else ChatAction(**common, type='chat', channel=public))
    req = replace(base, snapshot=snapshot, history=history, co=co, ability_results=ability,
        discussion=capture, action_context=BrainActionContext(1, 1, 1, True, (BrainActionOption('action:0', handle),)))
    return Case(f'G{group:02d}-{variant+1}', category, role, req, expected, rule)


def cases():
    return tuple(build_case(group, variant) for group in range(1,17) for variant in range(2))

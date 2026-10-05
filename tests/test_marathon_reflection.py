import asyncio
import json

import pytest
import yaml

from ai_agent.marathon_eval import (LESSON_SCHEMA, bounded_prompt, clean_lessons, content_and_preset,
                                   lesson_texts, reflection_input, reflection_rules, reflect_game,
                                   revalidate_notes)
from ai_agent.marathon_runtime import ROOT, read_json, save_json


class ReflectionLLM:
    thinking_tokens = 0
    context_limit = 8192

    def __init__(self, broken_phase=None, broken_attempts=0, *, token_divisor=4):
        self.broken_phase = broken_phase
        self.broken_attempts = broken_attempts
        self.token_divisor = token_divisor
        self.requests = []
        self.calls = []

    async def count_tokens(self, prompt, **kwargs):
        return len(json.dumps(prompt, ensure_ascii=False)) // self.token_divisor

    async def complete(self, prompt, *, purpose, schema, **kwargs):
        data = json.loads(prompt()[1]['content'])
        self.requests.append({'purpose': purpose, 'data': data, **kwargs})
        if purpose == self.broken_phase and self.broken_attempts:
            self.broken_attempts -= 1
            return '{broken'
        if purpose == 'reflection':
            return json.dumps({'role': [], 'general': [{'scene': '根拠を尋ねられた時',
                                'action': '受信した結果と推測を区別する', 'why': '事実を誤解しない'}]}, ensure_ascii=False)
        candidates = data.get('候補', data.get('照合対象', []))
        if purpose.startswith('discussion_'):
            return json.dumps({'opinions': [{'id': item['id'], 'stance': ('反対' if kwargs['player_id'] == 'player-0'
                                            and purpose == 'discussion_1' else '賛成'), 'reason': '能力の対象をルールに照合する',
                                            'revision': None} for item in candidates]}, ensure_ascii=False)
        if purpose in {'summary', 'deduplication'}:
            groups = {}
            for item in candidates:
                parts = item['text'].split('→')
                lesson = dict(zip(('scene', 'action', 'why'), parts)) if len(parts) == 3 else {
                    'scene': item['text'], 'action': 'ルールを確かめる', 'why': '判断の根拠にする'}
                groups[item['id']] = {'group_id': item['id'], 'relation': 'same', 'lesson': lesson}
            return json.dumps({'groups': groups}, ensure_ascii=False)
        if purpose == 'rule_check':
            facts = data['ルールの事実']
            fact = next(item['id'] for item in facts if '未判定の対象死者' in item['text'])
            text = candidates[0].get('text', '→'.join(str(value) for value in candidates[0].values()))
            return json.dumps({'rule_claims': ['教訓の前提を確認する'],
                               'contradicting_facts': [fact] if '生存者を霊能' in text else [],
                               'reason': '番号つき事実に照合した'}, ensure_ascii=False)
        raise AssertionError(purpose)


def notes():
    return {'medium': [{'text': '処刑死者がいる夜→サーバ候補を調べる→対象条件を守る', 'games': [1]},
                       {'text': '昼に生存者がいる→生存者を霊能で調べる→真偽を確かめる', 'games': [1]}]}


def game_files(tmp_path):
    game = tmp_path / 'game'
    roles = ['seer', 'medium', 'guard', 'werewolf', 'werewolf', 'madman', 'villager', 'villager', 'villager']
    save_json(game / 'server_record.json', {'roles': {f'player-{i}': role for i, role in enumerate(roles)},
                                          'rows': [], 'accepted_votes': [], 'private_results': []})
    save_json(game / 'checks.json', {'winner': 'village'})
    save_json(game / 'decisions.json', [])
    return game


def test_reflection_rules_reconstruct_every_yaml_without_changing_defaults():
    book = reflection_rules()
    neutral = yaml.safe_load((ROOT / 'content/ai_strategies_learning.yaml').read_text(encoding='utf-8'))['role_descriptions']
    for path in (ROOT / 'content/roles').glob('*.yaml'):
        source = yaml.safe_load(path.read_text(encoding='utf-8'))
        source['description'] = neutral.get(source['id'], source.get('description', ''))
        reconstructed = {**book['役職の共通項目'], **book['全役職の個別項目'][source['id']]}
        assert reconstructed == source
    assert 'count_as' not in book['役職の共通項目']
    assert '所属を隠して' not in json.dumps(book, ensure_ascii=False)
    assert '昼' in book['ルールの説明'] and 'CO' in book['ルールの説明']
    assert '第0夜(day=0)' in book['夜番号の読み方'] and '第1夜(day=1)' in book['夜番号の読み方']
    content, _ = content_and_preset()
    data = reflection_input({'roles': {'player-0': 'medium'}, 'rows': []}, [], 'player-0', {}, content)
    from ai_agent.rule_facts import rule_facts
    assert data['全役職とプリセットのルール']['ルールの事実'] == rule_facts()


def test_long_lessons_keep_id_filter_and_metadata_without_character_limit():
    long = '長い具体的な場面' * 120
    assert clean_lessons([{'scene': long, 'action': '行動', 'why': '理由'}, 'player-3に投票', 'プレイヤー4を護衛']) == [long + '→行動→理由']
    assert 'maxLength' not in json.dumps(LESSON_SCHEMA)
    texts = lesson_texts({'guard': [{'text': long + '→行動→理由', 'games': [1, 2]}],
                          'general': [{'text': '全員の教訓', 'games': [2]}]})
    assert len(texts['guard'][0]['text']) > 800
    assert texts['guard'][0]['support_games'] == 2
    assert texts['seer'] == [{'text': '全員の教訓', 'games': [2], 'support_games': 1, 'last_game': 2}]


def test_valid_json_discards_only_id_lessons_and_continues_discussion(tmp_path):
    class IDLessonLLM(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            if kwargs['purpose'] == 'reflection':
                self.requests.append({'purpose': kwargs['purpose'], **kwargs})
                return json.dumps({'role': [{'scene': 'player-3が疑われた時', 'action': '弁明する', 'why': '信用を得る'}],
                                   'general': [{'scene': '結果の根拠を聞かれた時', 'action': '受信した結果を示す',
                                                'why': '推測と事実を区別する'}]}, ensure_ascii=False)
            return await super().complete(prompt, **kwargs)

    async def work():
        llm = IDLessonLLM()
        updated = await reflect_game(llm, game_files(tmp_path), {}, tmp_path / 'history', 2)
        destination = tmp_path / 'history/game_0002'
        assert not (destination / 'failures.json').exists()
        assert not read_json(destination / 'stages.json')['fallback_rule_check_only']
        assert len(read_json(destination / 'discussion.json')['2']) == 9
        assert updated['general'] and 'player-' not in json.dumps(updated, ensure_ascii=False)
        assert all(read_json(destination / f'player-{i}.json')['role'] == [] for i in range(9))
    asyncio.run(work())


def test_bounded_prompt_only_samples_game_records():
    async def work():
        llm = ReflectionLLM(token_divisor=1)
        llm.context_limit = 500
        data = {'ルール': ['固定の全ルール'], '候補': ['全候補'], '全員の1巡目の意見': ['全意見'],
                '本人の発言': [{'message': '古い長い発言' * 200}, {'message': '新しい発言'}]}
        prompt = await bounded_prompt(llm, data, 'JSON', reserve=100)
        kept = json.loads(prompt[1]['content'])
        assert kept['ルール'] == data['ルール'] and kept['候補'] == data['候補']
        assert kept['全員の1巡目の意見'] == data['全員の1巡目の意見']
        assert data['本人の発言'][0]['message'] == '古い長い発言' * 200
        with pytest.raises(ValueError):
            await bounded_prompt(llm, {'候補': ['削ってはいけない' * 300]}, 'JSON', reserve=100)
    asyncio.run(work())


def test_nine_players_two_rounds_see_every_first_round_opinion(tmp_path):
    async def work():
        llm = ReflectionLLM()
        updated = await reflect_game(llm, game_files(tmp_path), notes(), tmp_path / 'history', 2)
        destination = tmp_path / 'history/game_0002'
        discussion = read_json(destination / 'discussion.json')
        assert len(discussion['1']) == len(discussion['2']) == 9
        ids = set(read_json(destination / 'stages.json')['candidate_ids'])
        for round_number in ('1', '2'):
            for opinions in discussion[round_number].values():
                assert {item['id'] for item in opinions} == ids
        for request in llm.requests:
            if request['purpose'] == 'reflection':
                assert request['thinking_tokens'] == 0
                assert request['request_options']['chat_template_kwargs']['enable_thinking'] is False
            else:
                assert request['thinking_tokens'] in {768, 576, 512, 384, 256}
                assert request['request_options']['chat_template_kwargs']['enable_thinking'] is True
            if request['purpose'] == 'discussion_2':
                expected = {item['id'] for item in request['data']['候補']}
                previous = request['data']['全員の1巡目の意見']
                assert len(previous) == 9
                assert all({item[0] for item in opinions} == expected for opinions in previous.values())
        checks = read_json(destination / 'rule_checks.json')
        assert {item['origin'] for item in checks} == {'old', 'summary'}
        assert len([item for item in checks if item['origin'] == 'old']) == 2
        assert len(updated['medium']) == 1 and '生存者を霊能' not in updated['medium'][0]['text']
        assert all(request['input_tokens'] + request['thinking_tokens'] + request['max_tokens'] + 32 <= 8192
                   for request in read_json(destination / 'requests.json'))
    asyncio.run(work())


def test_summary_candidates_have_one_fixed_assignment(tmp_path):
    from jsonschema import Draft202012Validator, ValidationError

    class FixedAssignmentLLM(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            if kwargs['purpose'] == 'summary':
                group_schema = kwargs['schema']['properties']['groups']
                assert group_schema['type'] == 'object' and group_schema['additionalProperties'] is False
                assert group_schema['required'] == list(group_schema['properties'])
                candidate_id = group_schema['required'][0]
                repeated = {'groups': {candidate_id: {'group_id': [candidate_id, candidate_id],
                             'relation': 'same', 'lesson': None}}}
                with pytest.raises(ValidationError):
                    Draft202012Validator(kwargs['schema']).validate(repeated)
            return await super().complete(prompt, **kwargs)

    async def work():
        llm = FixedAssignmentLLM()
        source = {'medium': [notes()['medium'][0]]}
        updated = await reflect_game(llm, game_files(tmp_path), source, tmp_path / 'history', 2)
        assert len(updated['medium']) == 1
        assert any(request['purpose'] == 'summary' for request in llm.requests)
        assert not (tmp_path / 'history/game_0002/failures.json').exists()
    asyncio.run(work())


@pytest.mark.parametrize('invalid_representative', [False, True])
def test_summary_fixed_assignments_merge_sources_and_validate_representative(tmp_path, invalid_representative):
    from ai_agent.marathon_eval import _StageFailed, _summarize

    class GroupLLM(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            if kwargs['purpose'] != 'summary':
                return await super().complete(prompt, **kwargs)
            data = json.loads(prompt()[1]['content'])
            first, second = [item['id'] for item in data['候補']]
            lesson = {'scene': '未判定の死者がいる夜', 'action': 'サーバの対象から調べる', 'why': '対象条件を守る'}
            return json.dumps({'groups': {
                first: {'group_id': second if invalid_representative else first, 'relation': 'same', 'lesson': lesson},
                second: {'group_id': first, 'relation': 'conflict', 'lesson': lesson if invalid_representative else None},
            }}, ensure_ascii=False)

    async def work():
        source = {'medium': [{'text': '処刑死者がいる夜→対象を調べる→未判定の情報を得る', 'games': [1]},
                             {'text': '突然死者がいる夜→対象を調べる→未判定の情報を得る', 'games': [2]}]}
        if invalid_representative:
            with pytest.raises(_StageFailed):
                await _summarize(GroupLLM(), source, {1: {}, 2: {}}, reflection_rules(), tmp_path, 3)
            failures = read_json(tmp_path / 'failures.json')
            assert [item['attempt'] for item in failures] == [1, 2]
            assert all('代表' in item['error'] for item in failures)
        else:
            updated = await _summarize(GroupLLM(), source, {1: {}, 2: {}}, reflection_rules(), tmp_path, 3)
            assert len(updated['medium']) == 1
            assert updated['medium'][0]['games'] == [1, 2]
            assert updated['medium'][0]['support_games'] == 2
            assert read_json(tmp_path / 'merge.json')['medium'][0]['relation'] == 'conflict'
    asyncio.run(work())


@pytest.mark.parametrize('phase', ['reflection', 'discussion_1', 'discussion_2', 'summary'])
def test_invalid_json_twice_records_failure_and_always_checks_rules(tmp_path, phase):
    async def work():
        llm = ReflectionLLM(phase, 2)
        updated = await reflect_game(llm, game_files(tmp_path), notes(), tmp_path / 'history', 2)
        destination = tmp_path / 'history/game_0002'
        assert len(updated['medium']) == 1
        failures = read_json(destination / 'failures.json')
        assert [item['attempt'] for item in failures if item['phase'] == phase] == [1, 2]
        assert read_json(destination / 'stages.json')['fallback_rule_check_only']
        assert any(request['purpose'] == 'rule_check' for request in llm.requests)
        if phase != 'summary':
            assert not any(request['purpose'] == 'summary' for request in llm.requests)
        if phase == 'reflection':
            assert not any(request['purpose'].startswith('discussion') for request in llm.requests)
    asyncio.run(work())


def test_rule_check_json_failure_rejects_candidates_and_preserves_original(tmp_path):
    async def work():
        original = tmp_path / 'notes.json'
        save_json(original, notes())
        llm = ReflectionLLM('rule_check', 2)
        updated = await revalidate_notes(llm, notes(), tmp_path / 'notes.v2.json', 6)
        assert updated == {'medium': []} and read_json(original) == notes()
        rejected = read_json(tmp_path / 'notes.v2.rejected.json')
        assert len(rejected) == 2 and [item['status'] for item in rejected] == [None, '矛盾']
        assert len(read_json(tmp_path / 'failures.json')) == 2
    asyncio.run(work())


def test_rule_check_coverage_retries_and_uncertain_strategy_survives(tmp_path):
    class CoverageLLM(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            if not self.requests:
                self.requests.append({'purpose': kwargs['purpose']})
                return json.dumps({'rule_claims': [], 'contradicting_facts': [9999], 'reason': '不明な番号'})
            return await super().complete(prompt, **kwargs)
    async def work():
        llm = CoverageLLM()
        source = {'guard': [{'text': '戦略の選択→根拠を話す→信用を得る', 'games': [1]}]}
        updated = await revalidate_notes(llm, source, tmp_path, 2)
        assert updated['guard'][0]['rule_check']['status'] == '合う'
        assert updated['guard'][0]['rule_check']['contradicting_facts'] == []
        assert len(read_json(tmp_path / 'failures.json')) == 1
    asyncio.run(work())


def test_all_notes_checked_before_eight_item_limit_and_ids_removed(tmp_path):
    async def work():
        source = {'guard': [{'text': f'場面{i}→候補を調べる→根拠を得る', 'games': [i + 1]} for i in range(9)] +
                           [{'text': 'player-3を護衛する', 'games': [20]}]}
        updated = await revalidate_notes(ReflectionLLM(), source, tmp_path, 20)
        assert len(updated['guard']) == 8
        checks = read_json(tmp_path / 'rule_checks.json')
        assert len(checks) == 10 and len({item['id'] for item in checks}) == 10
        reasons = {item['rejection_reason'] for item in read_json(tmp_path / 'rejected.json')}
        assert reasons == {'役職別・全員向けの8件上限', '空の教訓または参加者IDを含む教訓'}
    asyncio.run(work())


def test_old_notes_are_checked_without_discussing_them_again(tmp_path):
    async def work():
        llm = ReflectionLLM(token_divisor=3)
        source = {'guard': [{'text': f'場面{i}→候補を調べる→根拠を得る', 'games': [i + 1]} for i in range(10)]}
        await reflect_game(llm, game_files(tmp_path), source, tmp_path / 'history', 11)
        calls = [request for request in llm.requests if request['purpose'].startswith('discussion')]
        assert calls
        for player in (f'player-{i}' for i in range(9)):
            for purpose in ('discussion_1', 'discussion_2'):
                seen = [item['id'] for request in calls if request['player_id'] == player and request['purpose'] == purpose
                        for item in request['data']['候補']]
                assert len(seen) == len(set(seen)) == 1
                assert all(not candidate.startswith('old:') for candidate in seen)
                assert len([request for request in calls if request['player_id'] == player
                            and request['purpose'] == purpose]) == 1
        checks = read_json(tmp_path / 'history/game_0011/rule_checks.json')
        assert sum(item['origin'] == 'old' for item in checks) == 10
        assert {request['thinking_tokens'] for request in calls} <= {256, 384, 512}
    asyncio.run(work())

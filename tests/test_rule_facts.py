import asyncio
from dataclasses import replace
import json

from ai_agent.marathon import extend_deadline
from ai_agent.marathon_eval import _check_notes, _discuss, content_and_preset, reflect_game
from ai_agent.marathon_runtime import read_json, save_json
from ai_agent.rule_facts import rule_facts
from tests.test_marathon_reflection import ReflectionLLM, game_files


def test_facts_use_names_abilities_targets_and_preset_values_from_data():
    content, preset = content_and_preset()
    facts = rule_facts(content, preset)
    assert [fact['id'] for fact in facts] == list(range(1, len(facts) + 1))
    text = '\n'.join(fact['text'] for fact in facts)
    for expected in ['未判定', '処刑または突然死', '第1夜', '生存者', '襲撃', '本人は対象を選ばない',
                     '役職は分からない', '狂人', '自分は対象にできない', '票数だけ', '終了まで公開されない']:
        assert expected in text
    target = content.roles['guard']
    changed = replace(content, roles={**content.roles, target.id: replace(target, name='保護係',
                        abilities=(replace(target.abilities[0], available_from_night=2),))})
    rules = replace(preset.rules, guard=replace(preset.rules.guard, self_guard=True, consecutive=True))
    modified = '\n'.join(fact['text'] for fact in rule_facts(changed, replace(preset, rules=rules)))
    assert '保護係の護衛は第2夜' in modified and '狩人' not in modified
    assert '自分は対象にできない' not in modified and '連続する夜に同じ相手' not in modified


def test_contradiction_numbers_control_adoption_and_are_preserved(tmp_path):
    class Checker(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            data = json.loads(prompt()[1]['content'])
            assert len(data['照合対象']) == 1
            target = data['照合対象'][0]
            assert target['次の行動の方針'] == '違反として処刑' and 'text' in target
            assert set(target) == {'id', 'role', 'text', '観察した場面', '次の行動の方針', '理由', '行動方針の予定時期'}
            assert kwargs['request_options']['temperature'] == 0
            assert list(kwargs['schema']['properties']) == ['rule_claims', 'reason', 'contradicting_facts']
            self.requests.append({'purpose': kwargs['purpose'], **kwargs})
            return json.dumps({'rule_claims': ['騙りは許されない'], 'contradicting_facts': [2],
                               'reason': '名乗りの自由に反する'}, ensure_ascii=False)
    async def work():
        candidate = {'id': 'test:1', 'role': 'madman', 'text': '騙り→違反として処刑→禁止だから', 'games': [1]}
        updated = await _check_notes(Checker(), [candidate], rule_facts(), tmp_path, 1)
        assert updated == {'madman': []}
        row = read_json(tmp_path / 'rejected.json')[0]
        assert row['status'] == '矛盾' and row['contradicting_facts'] == [2]
        assert row['rule_claims'] == ['騙りは許されない']
    asyncio.run(work())


def test_second_round_only_discusses_disputed_new_candidates_in_one_call(tmp_path):
    class Opinions(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            data = json.loads(prompt()[1]['content'])
            self.requests.append({'purpose': kwargs['purpose'], 'data': data, **kwargs})
            return json.dumps({'opinions': [{'id': item['id'], 'stance': '反対' if
                kwargs['purpose'] == 'discussion_1' and kwargs['player_id'] == 'player-0'
                and item['index'] == 0 else '賛成', 'reason': '根拠を確認する', 'revision': None}
                for item in [{**item, 'index': int(item['id'].rsplit(':', 1)[1])} for item in data['候補']]]}, ensure_ascii=False)
    async def work():
        llm = Opinions()
        roles = {f'player-{i}': 'villager' for i in range(9)}
        pools = {'general': [{'text': f'場面{i}→根拠を確認→議論を進める', 'games': [1]} for i in range(12)]}
        rounds = await _discuss(llm, pools, roles, {'ルールの事実': rule_facts()}, tmp_path, 1,
                               {player: {} for player in roles})
        assert len(llm.requests) == 18
        assert all(len(rows) == 12 for rows in rounds[1].values())
        assert all([row['id'] for row in rows] == ['candidate:general:0'] for rows in rounds[2].values())
        assert all(len(request['data']['全員の1巡目の意見']) == 9 for request in llm.requests
                   if request['purpose'] == 'discussion_2')
    asyncio.run(work())


def test_reflection_records_wall_time_and_no_second_round_when_unanimous(tmp_path, monkeypatch):
    class Unanimous(ReflectionLLM):
        async def complete(self, prompt, **kwargs):
            raw = await super().complete(prompt, **kwargs)
            if kwargs['purpose'] == 'discussion_1':
                parsed = json.loads(raw)
                for item in parsed['opinions']:
                    item['stance'] = '賛成'
                return json.dumps(parsed, ensure_ascii=False)
            return raw
    tick = iter(range(1000))
    monkeypatch.setattr('ai_agent.marathon_eval.time.monotonic', lambda: next(tick))
    async def work():
        llm = Unanimous()
        await reflect_game(llm, game_files(tmp_path), {}, tmp_path / 'history', 1)
        summary = read_json(tmp_path / 'history/game_0001/timing_summary.json')
        assert summary['wall_sec'] > 0 and summary['within_30_minutes']
        assert summary['phase_sec']['discussion_1'] == 9
        assert not any(request['purpose'] == 'discussion_2' for request in llm.requests)
        assert read_json(tmp_path / 'history/game_0001/stages.json')['discussion_complete']
    asyncio.run(work())


def test_deadline_extension_keeps_exact_backup_and_other_state_fields(tmp_path):
    state = {'phase': 'B', 'finished': False, 'deadline': 500, 'round': 7, 'series': [{'notes': 'preserve'}]}
    path = tmp_path / 'state.json'
    save_json(path, state)
    before = path.read_bytes()
    result = extend_deadline(tmp_path, 38, now=1000)
    from pathlib import Path
    assert Path(result['backup']).read_bytes() == before
    updated = read_json(path)
    assert updated == {**state, 'deadline': 1000 + 38 * 3600}

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import yaml

from ai_agent.marathon import LocalBackend, Marathon, affordable, failure_count
from ai_agent.marathon_checkpoint import choose_checkpoint, fallback_choice, validate_choice
from ai_agent.marathon_eval import (bounded_prompt, clean_lessons, consolidate_notes, content_and_preset,
                                   game_metrics, grade_scene, lesson_texts, merge_candidates,
                                   reflect_game, reflection_input, scene_prompt, snapshot, solve_scenes)
from ai_agent.marathon_runtime import ModelServer, ROOT, make_llm, read_json, save_json, settings
from ai_agent.play import run_game
from ai_agent.llm import SharedLLM
from ai_agent.prompts import messages
from ai_agent.state import PlayerState
from tests.test_ai_agent import FakeLLM


def row(name='qwen35-9b/off', accuracy=.8, wall=600):
    return {'id': name, 'model': name.split('/')[0], 'mode': name.split('/')[1], 'rate': 1,
            'status': 'completed', 'scene': {'accuracy': accuracy}, 'game': {'completed': True, 'wall_sec': wall}}


def test_scenes_have_sources_and_private_input_only():
    bank = read_json(ROOT / 'content/marathon_scenes.json')
    assert 10 <= len(bank) <= 15
    for scene in bank:
        assert scene['source']['path'].startswith('games/')
        assert scene['correct'] and set(scene['correct']) <= set(scene['options'])
        assert grade_scene(scene, json.dumps({'answer': scene['correct'][0]}))
        assert not grade_scene(scene, '{bad json')
        assert not grade_scene(scene, {'answer': scene['correct'][0], 'label': 'correct'})
        assert 'roles' not in scene['state']
        assert '全員の公開された役職' not in json.dumps(scene_prompt(scene), ensure_ascii=False)


def test_snapshot_does_not_use_future_or_other_result():
    content, _ = content_and_preset()
    record = {'roles': {'player-0': 'seer', 'player-1': 'medium'}, 'rows': [], 'private_messages': [],
              'private_results': [{'t': 1, 'player_id': 'player-0', 'visibility': 'private', 'event_type': 'RESULT', 'event_payload': {'text': 'OWN'}},
                                  {'t': 1, 'player_id': 'player-1', 'visibility': 'private', 'event_type': 'RESULT', 'event_payload': {'text': 'OTHER'}},
                                  {'t': 3, 'player_id': 'player-0', 'visibility': 'private', 'event_type': 'RESULT', 'event_payload': {'text': 'FUTURE'}}]}
    state = snapshot(record, 'player-0', 2, content)
    assert 'OWN' in state.private_text()
    assert 'OTHER' not in state.private_text() and 'FUTURE' not in state.private_text()


def test_clock_rate_is_applied_to_received_deadline():
    state = PlayerState('player-0', clock_rate=.2, phase_ends_at=110)
    with patch('ai_agent.state.time.monotonic', return_value=1000):
        state.receive({'type': 'player.list', 'timestamp': 100, 'payload': {'players': []}})
    with patch('ai_agent.state.time.monotonic', return_value=1010):
        assert state.seconds_left() == pytest.approx(35)


def test_checkpoint_validation_and_ranked_fallback(tmp_path):
    rows = [row(), row('gemma4-12b/off', .9), row('gemma4-12b/on', .7)]
    choice = fallback_choice(rows, 20000)
    assert choice['control'] == 'gemma4-12b/off'
    assert len(choice['learning']) == 2
    assert validate_choice(choice, rows, 20000)
    assert not validate_choice(choice, rows, 100)
    wrong = {**choice, 'control': 'unlisted'}
    assert not validate_choice(wrong, rows, 20000)
    nan = {**choice, 'learning': [{'setting': 'qwen35-9b/off', 'rate': float('nan')}]}
    assert not validate_choice(nan, rows, 20000)
    def unavailable(*args):
        raise OSError('Codex unavailable')
    selected = choose_checkpoint(rows, 20000, tmp_path, call=unavailable)
    assert selected['source'] == 'criteria'
    assert selected['decision'] == choice
    assert choose_checkpoint(rows, 20000, tmp_path, call=lambda *_: pytest.fail('must not call twice')) == selected


def test_lessons_limits_ids_evidence_and_duplicate_games():
    assert clean_lessons(['player-3へ投票', 'player 4へ投票', 'プレイヤー5の護衛', '長'*81, '場面→行動→理由'] * 4, 3) == ['長'*81, '場面→行動→理由']
    notes = {'guard': [{'text': '圧力→CO→回避', 'games': [1], 'last_game': 1}]}
    reflections = {'player-0': {'role': ['圧力→CO→回避'], 'general': ['議論→確認→誤解防止']},
                   'player-1': {'role': [], 'general': ['議論→確認→誤解防止']}}
    pools = merge_candidates(notes, reflections, {'roles': {'player-0': 'guard', 'player-1': 'seer'}}, 2)
    updated = consolidate_notes(pools, {'guard': [{'text': '圧力→CO→回避', 'indices': [0, 1]}]}, 2)
    assert updated['guard'][0]['support_games'] == 2
    assert updated['general'][0]['support_games'] == 1
    many = {'guard': [{'text': f'場面{i}→行動→理由', 'games': [i+1], 'last_game': i+1} for i in range(20)]}
    assert len(consolidate_notes(many, {}, 20)['guard']) == 8
    texts = lesson_texts(updated)
    assert any('圧力' in item['text'] for item in texts['guard'])
    assert all('圧力' not in item['text'] for item in texts['seer'])
    assert any('議論' in item['text'] for item in texts['seer'])
    conflict = {'guard': [{'text': '圧力→CO→回避', 'games': [1, 2, 3], 'last_game': 3},
                          {'text': '圧力→沈黙→回避', 'games': [4], 'last_game': 4}]}
    resolved = consolidate_notes(conflict, {'guard': [{'text': '圧力→沈黙→回避', 'indices': [0, 1], 'relation': 'conflict'}]}, 4)
    assert resolved['guard'][0]['text'] == '圧力→沈黙→回避'
    assert resolved['guard'][0]['support_games'] == 4


def test_learning_profile_keeps_own_secrets_but_removes_advice():
    content, preset = content_and_preset()
    state = PlayerState('player-3', role_id='werewolf', players=['player-3', 'player-4'], alive={'player-3', 'player-4'},
                        teammates=['player-4'], private=[{'type': 'TEST', 'text': '本人のみ'}])
    strategy = yaml.safe_load((ROOT / 'content/ai_strategies_learning.yaml').read_text(encoding='utf-8'))
    prompt = json.dumps(messages(state, content.roles, dict(preset.role_counts), '判断してください', rules=preset.rules,
                               strategy_data=strategy), ensure_ascii=False)
    assert '人狼' in prompt and 'player-4' in prompt and '本人のみ' in prompt
    assert '目立たない' not in prompt and '終盤の票合わせ' not in prompt and '所属を隠して' not in prompt


def test_reflection_post_game_summary_contains_votes_and_only_own_decisions():
    content, _ = content_and_preset()
    record = {'roles': {'player-0': 'seer', 'player-1': 'werewolf'}, 'rows': [],
              'accepted_votes': [{'player_id': 'player-0', 'target_player_id': 'player-1', 'day': 1}]}
    decisions = [{'player_id': 'player-0', 'decision': {'aim': 'OWN'}}, {'player_id': 'player-1', 'decision': {'aim': 'OTHER'}}]
    data = reflection_input(record, decisions, 'player-0', {'winner': 'village'}, content)
    assert data['投票者と投票先'] == record['accepted_votes']
    assert data['全員の公開された役職']['player-1'] == '人狼'
    assert 'OTHER' not in json.dumps(data)


def test_metrics_votes_from_accepted_server_records():
    content, _ = content_and_preset()
    record = {'roles': {'player-0': 'werewolf', 'player-1': 'werewolf', 'player-2': 'guard'},
              'rows': [{'kind': 'PHASE_STARTED', 't': 0, 'payload': {'day': 1, 'phase': 'day'}},
                       {'kind': 'chat', 't': 1, 'message': {'player_id': 'player-0', 'message': 'player-2に投票します。'}},
                       {'kind': 'PHASE_STARTED', 't': 4, 'payload': {'day': 1, 'phase': 'vote'}},
                       {'kind': 'VOTE_RESOLVED', 't': 6, 'payload': {'day': 1, 'lynched_player_id': 'player-2'}}],
              'accepted_votes': [{'day': 1, 'player_id': 'player-0', 'target_player_id': 'player-1'},
                                 {'day': 1, 'player_id': 'player-1', 'target_player_id': 'player-2'}]}
    result = game_metrics(record, {}, content)
    assert result['wolf_mate_votes'] == 1
    assert result['guard_lynched_without_co'] == 1
    assert result['votes_for_suspects'][0]['share'] == .5
    assert result['vote_concentration'][0]['share'] == .5


def test_deadline_resume_and_consecutive_failure(tmp_path):
    clock = [1000]
    backend = type('Backend', (), {'close': lambda self: None})()
    runner = Marathon(tmp_path, backend, hours=1, now=lambda: clock[0])
    clock[0] += 50
    resumed = Marathon(tmp_path, backend, hours=38, now=lambda: clock[0])
    assert resumed.state['deadline'] == 4600
    assert resumed.remaining() == 3550
    assert affordable(1000, 600) and not affordable(500, 600)
    assert failure_count([{'key': 'a', 'status': 'failed'}, {'key': 'b', 'status': 'completed'}, {'key': 'a', 'status': 'failed'}], 'a') == 2


def test_context_bounds_trim_history_but_keep_own_information():
    async def work():
        llm = SharedLLM('http://fake/v1/chat/completions', context_limit=800)
        await llm.client.aclose()
        def handler(request):
            data = json.loads(request.content)
            if request.url.path == '/apply-template':
                return httpx.Response(200, json={'prompt': json.dumps(data['messages'], ensure_ascii=False)})
            return httpx.Response(200, json={'tokens': list(range(len(data['content'])))})
        llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        history = [{'text': '古い議論'*200}, {'text': '最新の議論'}]
        fitted = await llm.fit_context([{'role': 'system', 'content': '本人の役職と非公開結果を維持'},
                                      {'role': 'user', 'content': '今日の最近のチャット: '+json.dumps(history, ensure_ascii=False)+'\nいまの質問を日本語で答える'}], 100)
        assert '本人の役職と非公開結果を維持' in fitted[0]['content']
        assert '最新の議論' in fitted[1]['content'] and '古い議論' not in fitted[1]['content']
        assert 'いまの質問' in fitted[1]['content']
        await llm.client.aclose()
    asyncio.run(work())


def test_completed_game_is_recovered_without_running_again(tmp_path):
    backend = type('Backend', (), {'game': lambda *_args, **_kwargs: pytest.fail('completed game must be skipped')})()
    runner = Marathon(tmp_path, backend)
    destination = tmp_path / 'games' / 'interrupted_commit'
    save_json(destination/'checks.json', {'completed': True, 'crashes': 0, 'wall_sec': 600,
                                          'public_messages': 60, 'llm_calls': [], 'metrics': {}, 'mechanical_conditions': {}})
    save_json(destination/'server_record.json', {'roles': {}, 'rows': []})
    save_json(destination/'decisions.json', [])
    (destination/'transcript.md').write_text('完走', encoding='utf-8')
    runner.state['game_paths']['unit'] = str(destination)
    runner.save()
    resumed = Marathon(tmp_path, backend)
    assert resumed.play('unit', settings()[0], 1, 750)['completed']
    assert (destination/'experiment_metrics.json').exists()


def test_model_server_never_kills_reused_foreign_pid(tmp_path):
    server = ModelServer(tmp_path)
    server.owner = {'pid': 999, 'created': 'old', 'path': 'llama-server.exe'}
    with patch('ai_agent.marathon_runtime.process_inventory', return_value=[{'ProcessId': 999, 'CreationDate': 'new', 'ExecutablePath': 'llama-server.exe'}]), patch('ai_agent.marathon_runtime.powershell') as stop:
        server.stop()
        stop.assert_not_called()


def test_two_failures_disable_only_one_series(tmp_path):
    class Backend:
        def prepare(self, setting):
            raise RuntimeError('model unavailable')
        def failed(self):
            pass
    runner = Marathon(tmp_path, Backend())
    runner.state['phase'] = 'B'
    runner.state['series'] = [{'id': 'broken', 'setting': settings()[0], 'learning': False, 'notes': {}, 'estimate': 600, 'disabled': False}]
    runner.run_b(max_rounds=2)
    assert runner.state['series'][0]['disabled']
    assert len(runner.state['failures']) == 2


def test_a_only_runs_without_b_reserve_and_waits_on_resume(tmp_path):
    class Backend:
        deadline = 0
        played = []
        closed = 0
        def prepare(self, setting):
            return 0
        def scenes(self, *args):
            return {'accuracy': 1}
        def game(self, setting, seed, destination, timeout, **kwargs):
            self.played.append((setting['id'], seed))
            return {'completed': True, 'wall_sec': 600, 'public_messages': 60, 'llm_calls': 200,
                    'metrics': {}, 'experiment': {}, 'transcript': str(destination/'transcript.md')}
        def close(self):
            self.closed += 1
    backend = Backend()
    def never_pick(*args, **kwargs):
        pytest.fail('前半のみでは区切りのCodexと後半を実行しない')
    runner = Marathon(tmp_path, backend, hours=1, configurations=[settings()[0]], checkpoint=never_pick)
    runner.run(phase_a_only=True)
    assert backend.played == [('qwen35-9b/off', 1)]
    assert backend.closed == 1 and runner.state['phase'] == 'checkpoint'
    assert runner.state['phase_a_only'] and not runner.state['finished']
    resumed = Marathon(tmp_path, backend, checkpoint=never_pick)
    resumed.run()
    assert len(backend.played) == 1 and backend.closed == 2
    assert (tmp_path/'REPORT.md').exists()


def test_separate_b_deadline_is_started_once_and_a_must_finish(tmp_path):
    clock = [100]
    backend = type('Backend', (), {'deadline': 0})()
    runner = Marathon(tmp_path, backend, hours=1, now=lambda: clock[0])
    with pytest.raises(ValueError):
        runner.begin_b(38)
    runner.state.update(phase='checkpoint', phase_a_only=True)
    runner.save()
    clock[0] = 10000
    runner.begin_b(38)
    assert runner.state['deadline'] == 10000 + 38*3600
    assert backend.deadline == runner.state['deadline']
    assert not runner.state['phase_a_only']
    clock[0] += 600
    resumed = Marathon(tmp_path, backend, now=lambda: clock[0])
    resumed.begin_b(38)
    assert resumed.state['deadline'] == runner.state['deadline']


def test_worker_is_stopped_if_ownership_query_fails(tmp_path):
    backend = LocalBackend(tmp_path, 8091)
    with patch('ai_agent.marathon.subprocess.Popen') as spawn, patch('ai_agent.marathon.powershell', side_effect=OSError('CIM unavailable')):
        spawn.return_value.poll.return_value = None
        with pytest.raises(OSError):
            backend.game(settings()[0], 1, tmp_path/'game', 600)
        spawn.return_value.terminate.assert_called_once()


def test_fake_llm_a_checkpoint_b_reflection_and_resume(tmp_path):
    class ReflectionLLM:
        thinking_tokens = 0
        calls = []
        def __init__(self):
            def handler(request):
                data = json.loads(request.content)
                if request.url.path == '/apply-template':
                    return httpx.Response(200, json={'prompt': json.dumps(data['messages'], ensure_ascii=False)})
                return httpx.Response(200, json={'tokens': list(range(len(data['content'])//3))})
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            self.url = 'http://fake/v1/chat/completions'
        async def complete(self, prompt, *, schema, **kwargs):
            if 'role' in schema['properties']:
                return json.dumps({'role': [{'scene': '疑い', 'action': '根拠を聞く', 'why': '推理を確かめる'}],
                                   'general': [{'scene': '主張', 'action': '結果を確認', 'why': '誤解を減らす'}]}, ensure_ascii=False)
            data = json.loads(prompt()[1]['content'])
            candidates = data.get('候補', data.get('照合対象', []))
            if 'opinions' in schema['properties']:
                return json.dumps({'opinions': [{'id': item['id'], 'stance': '賛成', 'reason': 'ルールを確かめる',
                                                'revision': None} for item in candidates]}, ensure_ascii=False)
            if 'groups' in schema['properties']:
                return json.dumps({'groups': {item['id']: {'group_id': item['id'], 'relation': 'same',
                                              'lesson': dict(zip(('scene', 'action', 'why'), item['text'].split('→')))}
                                             for item in candidates}}, ensure_ascii=False)
            return json.dumps({'rule_claims': [], 'contradicting_facts': [], 'reason': 'ルールと一致'}, ensure_ascii=False)
    class Backend:
        def prepare(self, setting):
            return 0
        def scenes(self, setting, destination, scenes):
            class SceneLLM:
                calls = []
                async def complete(self, prompt, *, schema, **kwargs):
                    return json.dumps({'answer': schema['properties']['answer']['enum'][0]})
            return asyncio.run(solve_scenes(SceneLLM(), scenes, destination, repeats=1))
        def game(self, setting, seed, destination, timeout, **kwargs):
            result = asyncio.run(run_game(seed=seed, day=2, vote=2, night=2, timing_scale=.001,
                                          llm=FakeLLM(), output=destination, clock_rate=.5 if kwargs.get('lessons') else 1,
                                          strategy_data=yaml.safe_load((ROOT/'content/ai_strategies_learning.yaml').read_text(encoding='utf-8')) if kwargs['learning_profile'] else None))
            assert result.checks['completed'] and result.checks['crashes'] == 0
            save_json(destination/'experiment_metrics.json', {})
            return {'completed': result.checks['completed'], 'wall_sec': result.checks['wall_sec'],
                    'public_messages': result.checks['public_messages'], 'llm_calls': 10, 'metrics': result.checks['metrics'],
                    'mechanical': result.checks['mechanical_conditions'], 'experiment': {}, 'transcript': str(destination/'transcript.md')}
        def reflect(self, setting, game_dir, notes, history, number):
            async def work():
                llm = ReflectionLLM()
                try:
                    return await reflect_game(llm, game_dir, notes, history, number)
                finally:
                    await llm.client.aclose()
            return asyncio.run(work())
        def close(self):
            pass
        def failed(self):
            pass
    def checkpoint(rows, remaining, directory, **kwargs):
        return choose_checkpoint(rows, remaining, directory, call=lambda *_: {'learning': [{'setting': 'qwen35-9b/off', 'rate': 1}], 'control': 'qwen35-9b/off', 'reason': '試験'})
    runner = Marathon(tmp_path, Backend(), configurations=[settings()[0]], checkpoint=checkpoint)
    runner.run(max_rounds=1)
    assert runner.state['finished'] and not runner.state['failures']
    assert len(runner.state['a']) == 1 and len(list((tmp_path/'results').glob('*.json'))) == 2
    assert runner.state['series'][0]['notes'] and runner.state['series'][1]['notes'] == {}
    assert (tmp_path/'REPORT.md').exists()
    before = len(runner.state['units'])
    resumed = Marathon(tmp_path, Backend(), checkpoint=checkpoint)
    resumed.run()
    assert len(resumed.state['units']) == before

from ai_agent.marathon import LocalBackend, Marathon, note_snapshot
from ai_agent.marathon_runtime import read_json, save_json


def test_note_snapshot_keeps_original_and_selects_numeric_revision(tmp_path):
    original = tmp_path / 'game_0006' / 'notes.json'
    save_json(original, {'general': [{'text': 'old'}]})
    save_json(original.with_name('notes.v2.json'), {'general': [{'text': 'checked'}]})
    save_json(original.with_name('notes.v10.json'), {'general': [{'text': 'newest'}]})
    assert note_snapshot(tmp_path, 6).name == 'notes.v10.json'
    assert read_json(original)['general'][0]['text'] == 'old'


def test_resume_loads_revision_from_latest_completed_game_only(tmp_path):
    class Backend:
        pass

    state = {'version': 1, 'started': 1, 'deadline': 1000, 'phase': 'B',
             'settings': [], 'a': [], 'units': [], 'round': 7, 'active': None,
             'finished': False, 'failures': [], 'series': [
                 {'id': 'learn', 'learning': True, 'notes': {'general': [{'text': 'old'}]}, 'disabled': False},
                 {'id': 'control', 'learning': False, 'notes': {}, 'disabled': False},
             ]}
    save_json(tmp_path / 'state.json', state)
    save_json(tmp_path / 'results' / 'B_learn_0006.json', {})
    save_json(tmp_path / 'notes' / 'learn' / 'game_0006' / 'notes.json', state['series'][0]['notes'])
    checked = {'general': [{'text': 'checked'}]}
    save_json(tmp_path / 'notes' / 'learn' / 'game_0006' / 'notes.v2.json', checked)
    runner = Marathon(tmp_path, Backend(), now=lambda: 950)
    runner.run_b(max_rounds=0)
    assert runner.state['series'][0]['notes'] == checked
    assert runner.state['series'][1]['notes'] == {}
    assert runner.state['deadline'] == 1000

    save_json(tmp_path / 'results' / 'B_learn_0007.json', {})
    latest = {'general': [{'text': 'latest'}]}
    save_json(tmp_path / 'notes' / 'learn' / 'game_0007' / 'notes.json', latest)
    runner = Marathon(tmp_path, Backend(), now=lambda: 950)
    runner.run_b(max_rounds=0)
    assert runner.state['series'][0]['notes'] == latest


def test_backend_reflection_failure_keeps_checked_notes_and_records_reason(tmp_path, monkeypatch):
    class LLM:
        calls = []

        async def close(self):
            pass

    async def failed(*args, **kwargs):
        raise RuntimeError('test pipeline failure')

    monkeypatch.setattr('ai_agent.marathon.make_llm', lambda *_: LLM())
    monkeypatch.setattr('ai_agent.marathon.reflect_game', failed)
    backend = LocalBackend(tmp_path, 8091)
    checked = {'general': [{'text': 'checked', 'rule_check': {'status': '合う'}}]}
    history = tmp_path / 'notes'
    assert backend.reflect({}, tmp_path / 'game', checked, history, 7) == checked
    directory = history / 'game_0007'
    assert read_json(directory / 'notes.json') == checked
    assert read_json(directory / 'pipeline_failure.json')['error'] == 'RuntimeError: test pipeline failure'
    assert read_json(directory / 'calls.json') == []


def test_reflection_timeout_keeps_checked_notes_and_does_not_stop_runner(tmp_path, monkeypatch):
    class LLM:
        calls = []
        async def close(self):
            pass
    async def timeout(awaitable, seconds):
        assert seconds == 1800
        awaitable.close()
        raise TimeoutError('感想戦の30分上限')
    monkeypatch.setattr('ai_agent.marathon.make_llm', lambda *_: LLM())
    monkeypatch.setattr('ai_agent.marathon.asyncio.wait_for', timeout)
    source = {'general': [{'text': '照合済み教訓', 'games': [6]}]}
    backend = LocalBackend(tmp_path, 8091)
    assert backend.reflect({}, tmp_path / 'game', source, tmp_path / 'history', 7) == source
    assert 'TimeoutError' in read_json(tmp_path / 'history/game_0007/pipeline_failure.json')['error']


def test_resume_keeps_interrupted_worker_request_and_log_without_output_directory(tmp_path):
    class Backend:
        def game(self, setting, seed, destination, timeout, **kwargs):
            return {'destination': destination}

    key = 'B_learn_0007'
    request = tmp_path / 'games' / (key + '_request.json')
    log = tmp_path / 'games' / (key + '.log')
    save_json(request, {'lessons': 'original lessons'})
    log.write_text('original partial game', encoding='utf-8')
    runner = Marathon(tmp_path, Backend(), now=lambda: 1)
    result = runner.play(key, {}, 7, 10, learning_profile=True, lessons={'general': 'checked'})
    assert result['destination'].name.startswith(key + '_')
    assert read_json(request) == {'lessons': 'original lessons'}
    assert log.read_text(encoding='utf-8') == 'original partial game'

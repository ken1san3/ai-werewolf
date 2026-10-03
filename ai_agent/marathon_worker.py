"""Bounded game child; it never owns the model server or invokes git/Codex."""
import asyncio
from pathlib import Path
import sys
import yaml

from .marathon_eval import content_and_preset, game_metrics
from .marathon_runtime import ROOT, make_llm, read_json, save_json
from .play import run_game


async def game(request):
    llm = make_llm(request['setting'], request['port'])
    strategy = yaml.safe_load((ROOT / 'content/ai_strategies_learning.yaml').read_text(encoding='utf-8')) if request['learning_profile'] else None
    try:
        result = await run_game(seed=request['seed'], day=request.get('day', 90), vote=request.get('vote', 30),
                                night=request.get('night', 30), timeout=request['timeout'], llm=llm,
                                clock_rate=request['setting']['rate'], strategy_data=strategy,
                                lessons=request.get('lessons'), output=Path(request['output']), progress=True)
        content, _ = content_and_preset()
        directory = Path(request['output'])
        save_json(directory / 'llm_stats.json', llm.calls)
        save_json(directory / 'experiment_metrics.json', game_metrics(result.recorder.server_record, result.checks, content))
        return result.checks
    finally:
        await llm.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    asyncio.run(game(read_json(sys.argv[1])))

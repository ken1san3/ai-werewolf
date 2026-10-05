"""Numbered mechanics compiled from content, never from model output."""
from dataclasses import asdict
import json
from pathlib import Path

import yaml

from server.aiwolf_core import load_content, load_preset

ROOT = Path(__file__).resolve().parents[1]


def rule_facts(content=None, preset=None):
    if content is None or preset is None:
        content = load_content(ROOT / 'content')
        preset = load_preset(ROOT / 'content/presets/standard_9.yaml', content)
    data = yaml.safe_load((ROOT / 'content/ai_rule_facts.yaml').read_text(encoding='utf-8'))
    templates, rows = data['templates'], list(data['global'])
    groups = {}
    for role in content.roles.values():
        for ability in role.abilities:
            key = json.dumps(asdict(ability), ensure_ascii=False, sort_keys=True)
            groups.setdefault(key, (ability, []))[1].append(role.name)
    for ability, names in groups.values():
        roles = '・'.join(names)
        effects = {effect.id for effect in ability.effects}
        effect = '・'.join(content.effects[key].name for key in sorted(effects))
        tag = ability.target.options.get('tag', '')
        tag_names = '・'.join(role.name for role in content.roles.values() if tag in role.tags)
        target = data['selectors'][ability.target.selector].format(
            tag=tag_names or tag,
            causes='または'.join(content.death_causes[key].name for key in ability.target.options.get('causes', [])))
        restrictions = []
        for restriction in ability.restrictions:
            enabled = True
            if restriction.enabled_when:
                path, expected = restriction.enabled_when.split(' == ')
                value = asdict(preset.rules)
                for field in path.removeprefix('rules.').split('.'):
                    value = value[field]
                enabled = value == yaml.safe_load(expected)
            if enabled:
                restrictions.append(data['restrictions'][restriction.type])
        if ability.uses.per_night is not None:
            restrictions.append(f'一夜に{ability.uses.per_night}回まで')
        if ability.uses.per_game is not None:
            restrictions.append(f'ゲーム全体で{ability.uses.per_game}回まで')
        start = '初夜から毎晩使え' if ability.available_from_night == 0 else (
            f'第{ability.available_from_night}夜から使え、初夜には実行も成功結果の受信もできず')
        rows.append(templates['ability'].format(roles=roles, effect=effect, start=start, target=target,
                    count=ability.target.count, restrictions='（' + '、'.join(restrictions) + '）' if restrictions else ''))
        if ability.target.selector == 'unexamined_dead_by_cause':
            rows.append(templates['dead_target'].format(roles=roles))
            excluded = [value.name for key, value in content.death_causes.items()
                        if key not in ability.target.options['causes']]
            rows.append(templates['forbidden_death'].format(roles=roles, causes='・'.join(excluded)))
        if 'inspect' in effects and ability.available_from_night == 0:
            rows.append(templates['first_night'][preset.rules.first_night_seer].format(roles=roles))
            rows.append(templates['regular_inspect'].format(roles=roles))
        for key, attribute in [('inspect', 'inspect_result'), ('medium_inspect', 'medium_result')]:
            if key in effects:
                rows.append(templates['result'].format(roles=roles, effect=content.effects[key].name))
                values = {}
                for role in content.roles.values():
                    values.setdefault(getattr(role.attributes, attribute), []).append(role.name)
                for value, result_names in values.items():
                    rows.append(templates['result_group'].format(roles='・'.join(result_names),
                                effect=content.effects[key].name, result=data['results'][value]))
    names = [role.name for role in content.roles.values() if not role.abilities]
    if names:
        rows.append(templates['no_ability'].format(roles='・'.join(names)))
    rows.append(templates['vote'][preset.rules.vote.reveal])
    rows.append(templates['graveyard']['revealed' if preset.rules.graveyard.reveal_roles else 'hidden'])
    rows.append(templates['formal_co'].format(limit='回数制限なし' if preset.rules.co.max_per_day is None
                else f'1人1日{preset.rules.co.max_per_day}回まで'))
    names = [content.roles[key].name for key in sorted(preset.role_counts)
             if preset.rules.co.allow_villager_claim or content.roles[key].claimable]
    rows.append(templates['formal_targets'].format(roles='・'.join(names)))
    return [{'id': number, 'text': text} for number, text in enumerate(dict.fromkeys(rows), 1)]

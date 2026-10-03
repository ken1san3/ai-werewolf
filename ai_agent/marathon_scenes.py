"""Build the fixed scene bank once from existing games; live runs only read it."""
from dataclasses import asdict
import re
from .marathon_eval import content_and_preset, snapshot
from .marathon_runtime import ROOT, read_json, save_json
from .strategy import vote_pressure


def build_bank(destination):
    content, _ = content_and_preset()
    full = 'games/20261003_172053_804506_seed1'
    short = 'games/20261003_164356_163908_seed1'
    records = {p: read_json(ROOT / p / 'server_record.json') for p in (full, short)}
    bank = []

    def add(name, source, player, at, question, options, correct, criterion):
        state = snapshot(records[source], player, at, content)
        assert player in state.alive
        data = asdict(state)
        data['alive'] = sorted(state.alive)
        data['phase_ends_at'] = None
        bank.append({'id': name, 'source': {'path': source, 'at_sec': at,
                     'transcript_lines': [2 * i + 5 for i, r in enumerate(records[source]['rows']) if r['t'] <= at][-5:]},
                     'state': data, 'question': question, 'options': options, 'correct': correct, 'criterion': criterion})

    first = next(r['t'] for r in records[full]['rows'] if r['kind'] == 'PHASE_STARTED' and r['payload']['phase'] == 'day')
    add('seer_first_co', full, 'player-0', first, '初日の昼の最初です。自分の最初の発言を選んでください。',
        {'a': '占い師COし、受信したplayer-2の人狼ではない結果を出す', 'b': '市民COして占い結果を伏せる', 'c': 'player-2は人狼と告げる'}, ['a'], '本物の占い師の早期COと受信済み結果の一致')
    add('medium_first_co', full, 'player-6', first, '初日の昼の最初です。自分の最初の発言を選んでください。',
        {'a': '昨夜player-1を霊能で調べて人狼だったと告げる', 'b': '霊能者COし、初夜の結果はないと述べる', 'c': '結果がないので霊能COはルール違反だと述べる'}, ['b'], '結果なしの昼COは可能、初夜の霊能結果は作らない')
    guard_row = next(r for r in records[short]['rows'] if r['kind'] == 'chat' and r['message']['player_id'] != 'player-1' and vote_pressure('player-1', r['message']['message']))
    add('guard_pressure_co', short, 'player-1', guard_row['t'], '自分への投票・処刑の呼びかけが出ました。投票前の行動を選んでください。',
        {'a': '護衛能力を忘れて市民のつもりで答える', 'b': '無言で処刑を受け入れる', 'c': '狩人COし、受理された護衛先を示して処刑を避ける'}, ['c'], '狩人が自分への処刑圧力に投票前に防御COする')
    mate = next(r for r in records[full]['rows'] if r['kind'] == 'chat' and r['message']['player_id'] not in {'player-3', 'player-4'} and 'player-4' in r['message']['message'] and re.search('疑|黒|怪|処刑', r['message']['message']))
    add('wolf_mate_suspected', full, 'player-3', mate['t'], '通知された仲間が疑われています。まだ仲間へ投票する必要が確定しない場面です。方針を選んでください。',
        {'a': '仲間player-4の処刑を自分から推進し票を入れる', 'b': '仲間以外の生存者を公開の根拠で検討し、仲間への処刑誘導を避ける', 'c': '初日から自分と仲間の正体を全公開する'}, ['b'], '公開の定石: 不要な仲間への投票・処刑誘導を避ける')
    add('medium_living_target', full, 'player-6', first + 30, '生存しているplayer-1の霊能結果を聞かれたら何を答えますか？',
        {'a': '生存者は霊能判定できず、受信結果もないと答える', 'b': '人狼確定と答える', 'c': '護衛先から村人確定と答える'}, ['a'], '霊能の対象と本人の受信済み結果のルール')
    add('white_is_not_team', full, 'player-0', first + 30, '自分のplayer-2の白結果から確定することを選んでください。',
        {'a': 'player-2は市民という役職に確定', 'b': 'player-2は市民陣営に確定', 'c': 'player-2は人狼ではないが、狂人の可能性は残る'}, ['c'], 'inspect_resultと陣営を混同しない')
    city_row = next(r for r in records[full]['rows'] if r['kind'] == 'chat' and r['message']['player_id'] != 'player-5' and 'player-5' in r['message']['message'])
    add('own_id_defense', full, 'player-5', city_row['t'], '自分player-5について聞かれました。自分の立場を説明する文を選んでください。',
        {'a': 'player-5はどう考えていますか？', 'b': '私は市民です。私の見方を説明します。', 'c': '私はplayer-7です。'}, ['b'], '本人IDを他人として扱わず、一人称で答える')
    voting = next(r for r in records[full]['rows'] if r['kind'] == 'PHASE_STARTED' and r['payload']['phase'] == 'vote')
    add('vote_follows_discussion', full, 'player-5', voting['t'], '直前の昼に、自分や他の人が疑いを向けた生存者へ投票するなら誰ですか？',
        {'a': 'player-4', 'b': 'player-5（自分）', 'c': 'まだ議論されていない自分以外の人を無作為に選ぶ'}, ['a'], 'この昼に本人が人狼候補と述べ、複数の参加者が根拠を問うplayer-4への投票')
    night1 = next(r for r in records[full]['rows'] if r['kind'] == 'PHASE_STARTED' and r['payload']['phase'] == 'night' and r['payload']['day'] == 1)
    add('wolf_attack_target', full, 'player-3', night1['t'], '夜の襲撃対象の条件を選んでください。',
        {'a': '通知された仲間を襲撃して人数を減らす', 'b': '生存する仲間以外でサーバの候補から選ぶ', 'c': '死者を襲撃する'}, ['b'], '仲間の知識と生存対象のルールを守る')
    add('guard_self_target', full, 'player-1', night1['t'], 'このプリセットで今夜の護衛対象の条件を選んでください。',
        {'a': '自分を護衛する', 'b': '死者を護衛する', 'c': '自分以外の生存者でサーバの候補から選ぶ'}, ['c'], 'プリセットは自己護衛禁止、生存する合法対象のみ')
    night0 = next(r['t'] for r in records[full]['rows'] if r['kind'] == 'PHASE_STARTED' and r['payload']['phase'] == 'night0')
    add('seer_first_night', full, 'player-0', night0, '初夜の占いで本人が行える選択を選んでください。',
        {'a': '占い対象を自分で選んで黒判定を得る', 'b': 'サーバの自動の白結果を待つ。本人は対象を選ばない', 'c': '霊能の対象を選ぶ'}, ['b'], 'random_white初夜のルール')
    add('medium_first_night', full, 'player-6', night0, '初夜に自分ができる能力行動を選んでください。',
        {'a': '生存者を霊能で調べる', 'b': '襲撃された人を調べる', 'c': '能力を使わず、初夜には結果がない'}, ['c'], 'available_from_night=1、初夜の霊能行動なし')
    save_json(destination, bank)
    return bank

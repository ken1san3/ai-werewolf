"""Role claims and ability hints, with role-specific wording defined in YAML."""
import re

from .repetition import normalize
from .strategy import STRATEGIES


def unquoted(text, role_names):
    text = normalize(text)
    for name in role_names:
        text = text.replace(f'「{name}」', name).replace(f'『{name}』', name)
    return re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^\"]*"', '', text)


def team_claim(text, role_names):
    plain = unquoted(text, role_names)
    for clause in re.split(r'[。！？.!?]', plain):
        if re.search(r'もし|なら|だったら|とすれば|救うのでは(?:ない|ありません)|助けて(?:いない|いません)|仲間では(?:ない|ありません)|とは言っていない', clause):
            continue
        if any(re.search(pattern, clause) for pattern in STRATEGIES.get('team_claim_negative_patterns', [])):
            continue
        if any(re.search(pattern, clause) for pattern in STRATEGIES['team_claim_patterns']):
            return True
    return False


def self_claims(player, text, role_names):
    text = unquoted(text, role_names)
    text = re.sub(r'(私たち|私|僕|俺)\s*\(\s*player-\d+\s*\)\s*', r'\1', text)
    text = re.sub(r'[^。！？]*(?:調べた|占った|護衛した|守った)(?:そう|らしい)[^。！？]*', '', text)
    claims = []
    subject = rf'(?:私たち|我々|私|僕|俺|自分)(?:の(?:本当の)?役職)?(?:は|が|も|こそ(?:は|が)?|[、,])\s*|{re.escape(player)}\s*(?:さん)?\s*(?:は|が|も)\s*'
    tail = r'\s*(?:(?:の\s*)?co\s*)?(?:役職)?(?:です|でした|だった|します|しました|しています|している|だ(?:[。！、\s]|$)|であり|である|なので|なんです|だが|だけど|の一人|として|という役職|(?=[。！]|$))(?!\s*(?:可能性|かもしれ|なら|だったら|とすれば))'
    for name in role_names:
        data = STRATEGIES['role_claims'].get(name, {})
        role_word = '(?:' + '|'.join(re.escape(n) for n in [name, *data.get('aliases', [])]) + ')'
        named_intro = rf'(?:^|(?<=[。！？]))\s*{role_word}の\s*{re.escape(player)}\s*です'
        body = r'(?:(?!player-\d+\s*(?:さん)?\s*(?:は|が))[^。！？.!?])*?'
        own_predicate = rf'{re.escape(player)}\s*(?:さん)?\s*(?:は|が){body}{role_word}{tail}'
        bare = rf'(?:^|(?<=[。！？]))\s*(?:真の|本当の|唯一の)?{role_word}{tail}(?!\s*player-\d+)'
        inverse = rf'{role_word}(?:である|としての|の)(?:私たち|私|僕|俺|自分|{re.escape(player)})'
        named_members = rf'{role_word}(?:は|が)\s*(?:私たち|私|僕|俺|自分)(?:と\s*player-\d+|です|[。！])'
        alternatives = '|'.join(re.escape(n) for n in role_names)
        changed_role = rf'(?:{subject})(?:{alternatives})(?:\s*co)?\s*ではなく\s*[、,]?\s*{role_word}{tail}'
        possessed = rf'私には\s*{role_word}(?:の)?役職(?:が(?:あります|ある|あり|あって)(?!ません|かもしれ|なら|とすれば)|を(?:持|与))'
        pattern = rf'(?:{subject})(?:真の|本当の|唯一の)?{role_word}{tail}|{bare}|{named_intro}|{own_predicate}|{inverse}|{named_members}|{changed_role}|{possessed}'
        definite = False
        for match in re.finditer(pattern, text):
            if re.fullmatch(possessed, match.group()):
                predicate = re.split(r'[。！？.!?、,]', text[match.start():], maxsplit=1)[0]
                if re.search(r'役職(?:が|を).{0,24}(?:わけ|訳|ということ|という意味)では(?:ない|ありません)|役職(?:が|を).{0,24}とは言って(?:いない|いません)', predicate):
                    continue
            context = re.split(r'[。！？.!?]', text[:match.start()])[-1] + match.group()
            conditional = re.search(r'なら|だったら|とすれば|仮に|もし', context)
            role_as = re.search(rf'{role_word}(?:役職)?として', match.group())
            self_hypothesis = re.search(rf'(?:もし|仮に)\s*(?:私|僕|俺|{re.escape(player)})', context)
            if not conditional or re.fullmatch(inverse, match.group()) or (role_as and not self_hypothesis):
                definite = True
        if definite:
            claims.append(name)
            continue
        for clause in re.split(r'[。！？.!?]', text):
            for pattern in data.get('ability_patterns', []):
                action = re.search(pattern, clause)
                if not action:
                    continue
                other_subject = re.search(rf'(?!{re.escape(player)}\b)player-\d+\s*(?:さん)?\s*(?:は|が)', action.group())
                own_hypothesis = re.search(rf'(?:もし|仮に)\s*(?:私|僕|俺|{re.escape(player)})|(?:私|僕|俺|自分)(?:は|が)\s*{role_word}なら', clause)
                conditional = re.search(r'なら|だったら|とすれば|仮に|もし', clause)
                possessive = re.search(r'(?:私|僕|俺|自分)の', action.group())
                negated = any(re.search(p, clause[action.start():]) for p in data.get('negative_patterns', []))
                if not other_subject and not own_hypothesis and not negated and (not conditional or possessive):
                    definite = True
                    break
            if definite:
                claims.append(name)
                break
    return claims

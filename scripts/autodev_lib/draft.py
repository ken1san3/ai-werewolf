"""Qwen-authored narrow refinement; whitespace validation tightened by Implementer."""
import copy


def refine(template, reply):
    if not isinstance(reply, dict) or set(reply) != {'goal', 'context'}:
        raise ValueError('reply must contain exactly goal and context')
    for key in ('goal', 'context'):
        if not isinstance(reply[key], str) or not reply[key].strip():
            raise ValueError('nonempty goal/context required')
    result = copy.deepcopy(template)
    result.update(reply)
    return result

"""The synthetic provider must satisfy the real repetition contract."""
from dataclasses import replace
import json

import pytest

from ai_client.brain import ChatDecision
from ai_client.brain.controller import _invalid_or_repeated_self_text
from ai_client.llm.decision import parse_llm_output
from ai_client.llm.prompt import project_brain_input
from tests.fixtures.phase6_semantic_backend import semantic_response
from tests.test_phase6_memory_projection import _chats
from tests.test_phase6_quality_grounding import CONFIG, request_with
from tests.test_phase6_semantic_completion import projected


@pytest.mark.parametrize("stance", ["support", "oppose"])
def test_fixture_peer_agreement_survives_real_validation_and_copy_guard(stance):
    first = semantic_response(projected(text=f"{stance} this proposal?"))
    peer_chat = replace(_chats(1)[0], message=first["decision"]["message"])
    request = request_with(trigger="PEER_CHAT", chats=(peer_chat,))
    projection = project_brain_input(request, config=CONFIG)
    response = semantic_response(json.loads(projection.messages[1].content))
    parsed = parse_llm_output(json.dumps(response), projection=projection)
    assert response["discussion"]["speech_act"]["kind"] == "ANSWER"
    assert response["discussion"]["speech_act"]["stance"] == stance.upper()
    assert response["discussion"]["speech_act"]["in_reply_to"]["order"] == peer_chat.order
    assert not _invalid_or_repeated_self_text(request, parsed.decision)
    # Permitting a short peer agreement must not permit self repetition.
    own_chat = replace(peer_chat, player_id="opaque-self")
    own = request_with(chats=(own_chat,))
    assert _invalid_or_repeated_self_text(own, parsed.decision)


def test_former_fixture_long_copy_is_still_rejected():
    former = "I support that proposal; let us compare the evidence."
    request = request_with(chats=(replace(_chats(1)[0], message=former),))
    assert _invalid_or_repeated_self_text(request, ChatDecision("action:0", former))

"""Offline v2 stage settings. These values do not authorize provider execution."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

STAGES = ("chat_plan", "message", "pre_vote", "co_opportunity", "ability")


def stage_seed(capture_id: str, stage: str, attempt: int) -> int:
    if not isinstance(capture_id, str) or not capture_id or stage not in STAGES:
        raise ValueError("invalid v2 stage identity")
    if type(attempt) is not int or attempt not in (1, 2):
        raise ValueError("invalid v2 attempt")
    material = capture_id.encode("utf-8") + b"\x00" + stage.encode("utf-8") + b"\x00" + attempt.to_bytes(4, "big")
    return int.from_bytes(hashlib.sha256(material).digest()[:4], "big")


@dataclass(frozen=True)
class GenerationSettingsV2:
    chat_plan_max_output_tokens: int | None = None
    message_max_output_tokens: int | None = None
    pre_vote_max_output_tokens: int | None = None
    co_max_output_tokens: int | None = None
    ability_max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for stage in STAGES:
            value = getattr(self, ("co" if stage == "co_opportunity" else stage) + "_max_output_tokens")
            if value is not None and (type(value) is not int or not 1 <= value <= 512):
                raise ValueError("invalid v2 token ceiling")

    def for_stage(self, stage: str) -> int:
        if stage not in STAGES:
            raise ValueError("invalid v2 stage")
        value = getattr(self, ("co" if stage == "co_opportunity" else stage) + "_max_output_tokens")
        if value is None:
            raise ValueError("V2_BUDGET_UNSET")
        return value

    def reserved_tokens(self, stage: str) -> int:
        if stage == "chat_plan":
            return 2 * self.for_stage(stage) + 2 * self.for_stage("message")
        return 2 * self.for_stage(stage)

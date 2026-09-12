"""Deterministic schema-bound backend used by Phase 4 completion tests."""

from __future__ import annotations

from collections.abc import Mapping
import json

from ai_client.llm import (
    BackendIdentity,
    LLMUsage,
    StructuredGenerationResponse,
)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


class Phase4FakeBackend:
    """Choose only constants/enums exposed by one dynamic decision schema."""

    def __init__(self) -> None:
        self.identity = BackendIdentity(
            "phase4_test_fake",
            "http://127.0.0.1:1",
            "/v1/chat/completions",
            "deterministic-test-model",
            "4" * 64,
        )
        self.calls: list[dict[str, object]] = []
        self.active = 0
        self.max_active = 0
        self.closed = False

    async def generate(self, request: object) -> StructuredGenerationResponse:
        if self.closed:
            raise RuntimeError("fake backend is closed")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            schema = _plain(request.output_schema)
            messages = tuple(request.messages)
            payload = json.loads(messages[1].content)
            offered = payload["action_context"]["options"]
            branches = schema["oneOf"]
            branch = self._select_branch(branches)
            decision = self._decision_for(branch)
            option_id = decision.get("option_id")
            selected_option = next(
                (item for item in offered if item["option_id"] == option_id), None
            )
            valid_targets = [] if selected_option is None else list(
                selected_option.get("valid_targets", [])
            )
            selected_targets: list[str] = []
            if decision["kind"] == "vote" and decision["target_player_id"] is not None:
                selected_targets = [decision["target_player_id"]]
            elif decision["kind"] == "ability":
                selected_targets = list(decision["target_player_ids"])
            self.calls.append(
                {
                    "ordinal": len(self.calls) + 1,
                    "request_id": request.request_id,
                    "kind": decision["kind"],
                    "option_id": option_id,
                    "offered_option_ids": [item["option_id"] for item in offered],
                    "selected_targets": selected_targets,
                    "valid_targets": valid_targets,
                    "membership_valid": (
                        option_id is None
                        or (
                            selected_option is not None
                            and all(target in valid_targets for target in selected_targets)
                        )
                    ),
                }
            )
            return StructuredGenerationResponse(
                request.request_id,
                json.dumps(decision, separators=(",", ":")),
                "deterministic-test-model",
                "stop",
                LLMUsage(1, 1),
            )
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        self.closed = True

    @staticmethod
    def _select_branch(branches: list[dict[str, object]]) -> dict[str, object]:
        by_kind = {
            branch["properties"]["kind"]["const"]: branch
            for branch in branches
        }
        for kind in ("vote", "ability", "chat", "co_declare", "none"):
            if kind in by_kind:
                return by_kind[kind]
        raise AssertionError("decision schema has no supported branch")

    @staticmethod
    def _decision_for(branch: dict[str, object]) -> dict[str, object]:
        properties = branch["properties"]
        kind = properties["kind"]["const"]
        if kind == "none":
            return {"kind": "none"}
        decision: dict[str, object] = {
            "kind": kind,
            "option_id": properties["option_id"]["const"],
        }
        if kind == "chat":
            decision["message"] = "phase4 deterministic chat"
        elif kind == "vote":
            values = properties["target_player_id"]["enum"]
            decision["target_player_id"] = next(
                (value for value in values if value is not None), None
            )
        elif kind == "ability":
            target_schema = properties["target_player_ids"]
            decision["target_player_ids"] = target_schema["items"]["enum"][
                : target_schema["minItems"]
            ]
        elif kind == "co_declare":
            decision["claimed_role_id"] = properties["claimed_role_id"]["enum"][0]
            decision["comment"] = "phase4 deterministic claim"
        else:
            raise AssertionError(f"unsupported decision branch: {kind}")
        return decision

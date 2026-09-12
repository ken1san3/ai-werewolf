"""Request-local deterministic backend used only behind the Phase 5 broker."""

from __future__ import annotations

from collections.abc import Mapping
import asyncio
import hashlib
import json

from ai_client.llm import (
    BackendIdentity,
    LLMUsage,
    ProviderTiming,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


class Phase5DeterministicBrokerBackend:
    """Select only constants and targets supplied by the current request schema."""

    def __init__(self) -> None:
        self._identity = BackendIdentity(
            backend_type="phase5_test_broker_backend",
            endpoint_origin="http://127.0.0.1:1",
            endpoint_path="/v1/chat/completions",
            model="deterministic-test-model",
            config_fingerprint=hashlib.sha256(b"phase5-offline-backend-v1").hexdigest(),
        )
        self.calls: list[dict[str, object]] = []
        self.active = 0
        self.peak_active = 0
        self.closed = False

    @property
    def identity(self) -> BackendIdentity:
        return self._identity

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        if self.closed:
            raise RuntimeError("deterministic backend is closed")
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            # Yield once so overlap would be observable if the broker ever
            # violated its one-provider-call invariant.
            await asyncio.sleep(0)
            schema = _plain(request.output_schema)
            canonical = json.loads(request.messages[1].content)
            offered = canonical["action_context"]["options"]
            self_player_id = canonical["snapshot"]["self"]["player_id"]
            branches = schema["oneOf"]
            branch = self._select_branch(branches)
            decision = self._decision_for(branch, self_player_id)
            option_id = decision.get("option_id")
            selected_option = next(
                (item for item in offered if item["option_id"] == option_id), None
            )
            valid_targets = (
                []
                if selected_option is None
                else list(selected_option.get("valid_targets", []))
            )
            selected_targets: list[str] = []
            if decision["kind"] == "vote" and decision["target_player_id"] is not None:
                selected_targets = [decision["target_player_id"]]
            elif decision["kind"] == "ability":
                selected_targets = list(decision["target_player_ids"])
            text = json.dumps(decision, ensure_ascii=False, separators=(",", ":"))
            self.calls.append(
                {
                    "ordinal": len(self.calls) + 1,
                    "request_id": request.request_id,
                    "self_player_id": self_player_id,
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
                    "response_chars": len(decision.get("message", "")),
                    "response_utf8_bytes": len(
                        str(decision.get("message", "")).encode("utf-8")
                    ),
                }
            )
            return StructuredGenerationResponse(
                request_id=request.request_id,
                text=text,
                provider_model=self.identity.model,
                finish_reason="stop",
                usage=LLMUsage(prompt_tokens=3, completion_tokens=2),
                provider_timing=ProviderTiming(
                    prompt_microseconds=10,
                    completion_microseconds=10,
                    prompt_tokens=3,
                    completion_tokens=2,
                ),
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
        for kind in ("vote", "ability", "chat", "co_declare", "co_report", "none"):
            if kind in by_kind:
                return by_kind[kind]
        raise AssertionError("decision schema has no supported branch")

    @staticmethod
    def _decision_for(
        branch: dict[str, object], self_player_id: str
    ) -> dict[str, object]:
        properties = branch["properties"]
        kind = properties["kind"]["const"]
        if kind == "none":
            return {"kind": "none"}
        decision: dict[str, object] = {
            "kind": kind,
            "option_id": properties["option_id"]["const"],
        }
        if kind == "chat":
            decision["message"] = f"hello from {self_player_id}"
        elif kind == "vote":
            values = properties["target_player_id"]["enum"]
            decision["target_player_id"] = next(
                (value for value in values if value is not None), None
            )
        elif kind == "ability":
            targets = properties["target_player_ids"]
            decision["target_player_ids"] = targets["items"]["enum"][
                : targets["minItems"]
            ]
        elif kind == "co_declare":
            decision["claimed_role_id"] = properties["claimed_role_id"]["enum"][0]
            decision["comment"] = f"claim from {self_player_id}"
        elif kind == "co_report":
            decision["report_kind"] = properties["report_kind"]["enum"][0]
            decision["target_player_id"] = properties["target_player_id"]["enum"][0]
            decision["claimed_result"] = properties["claimed_result"]["enum"][0]
        else:
            raise AssertionError(f"unsupported decision branch: {kind}")
        return decision

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from server.aiwolf_core import load_content
from server.aiwolf_core.actions import (
    ACTION_EFFECT_DISPATCH_IDS,
    ACTION_RESTRICTION_DISPATCH_IDS,
)
from server.aiwolf_core.capabilities import (
    IMPLEMENTED_EFFECT_IDS,
    IMPLEMENTED_PASSIVE_EFFECT_IDS,
    IMPLEMENTED_PASSIVE_IDS,
    IMPLEMENTED_RESTRICTION_TYPE_IDS,
    IMPLEMENTED_SELECTOR_IDS,
)
from server.aiwolf_core.content import _WIN_CONDITION_TYPES
from server.aiwolf_core.death import PASSIVE_DISPATCH_IDS, PASSIVE_EFFECT_DISPATCH_IDS
from server.aiwolf_core.phase import DAWN_PASSIVE_DISPATCH_IDS, DAWN_PASSIVE_EFFECT_DISPATCH_IDS
from server.aiwolf_core.targets import TARGET_SELECTOR_DISPATCH_IDS
from server.aiwolf_core.wins import PRIMARY_WIN_CONDITION_DISPATCH_IDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
AI_CLIENT_ROOT = PROJECT_ROOT / "ai_client"


class RuntimeCapabilityRegistryTests(unittest.TestCase):
    def test_production_ai_client_has_no_server_import(self) -> None:
        violations: list[str] = []
        for path in sorted(AI_CLIENT_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: tuple[str, ...]
                if isinstance(node, ast.Import):
                    modules = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    modules = (() if node.module is None else (node.module,))
                else:
                    continue
                if any(module == "server" or module.startswith("server.") for module in modules):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_implemented_capabilities_are_registered_content_vocabulary(self) -> None:
        content = load_content(CONTENT_ROOT)

        self.assertTrue(IMPLEMENTED_EFFECT_IDS.issubset(content.effects))
        self.assertTrue(IMPLEMENTED_PASSIVE_IDS.issubset(content.passives))
        self.assertTrue(IMPLEMENTED_SELECTOR_IDS.issubset(content.selectors))
        self.assertTrue(IMPLEMENTED_RESTRICTION_TYPE_IDS.issubset(content.restriction_types))

    def test_implemented_capabilities_match_their_resolver_dispatches(self) -> None:
        self.assertEqual(IMPLEMENTED_EFFECT_IDS, ACTION_EFFECT_DISPATCH_IDS)
        self.assertEqual(
            IMPLEMENTED_PASSIVE_IDS,
            PASSIVE_DISPATCH_IDS | DAWN_PASSIVE_DISPATCH_IDS,
        )
        self.assertEqual(
            IMPLEMENTED_PASSIVE_EFFECT_IDS,
            PASSIVE_EFFECT_DISPATCH_IDS | DAWN_PASSIVE_EFFECT_DISPATCH_IDS,
        )
        self.assertEqual(IMPLEMENTED_SELECTOR_IDS, TARGET_SELECTOR_DISPATCH_IDS)
        self.assertEqual(IMPLEMENTED_RESTRICTION_TYPE_IDS, ACTION_RESTRICTION_DISPATCH_IDS)

    def test_win_condition_vocabulary_matches_the_evaluator_dispatch(self) -> None:
        self.assertEqual(
            _WIN_CONDITION_TYPES,
            PRIMARY_WIN_CONDITION_DISPATCH_IDS | {"survive_when_others_win"},
        )


if __name__ == "__main__":
    unittest.main()

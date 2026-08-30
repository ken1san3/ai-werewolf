from __future__ import annotations

from pathlib import Path
import unittest

from server.aiwolf_core import load_content
from server.aiwolf_core.actions import (
    ACTION_EFFECT_DISPATCH_IDS,
    ACTION_RESTRICTION_DISPATCH_IDS,
)
from server.aiwolf_core.capabilities import (
    IMPLEMENTED_EFFECT_IDS,
    IMPLEMENTED_PASSIVE_IDS,
    IMPLEMENTED_RESTRICTION_TYPE_IDS,
    IMPLEMENTED_SELECTOR_IDS,
)
from server.aiwolf_core.death import PASSIVE_DISPATCH_IDS
from server.aiwolf_core.targets import TARGET_SELECTOR_DISPATCH_IDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"


class RuntimeCapabilityRegistryTests(unittest.TestCase):
    def test_implemented_capabilities_are_registered_content_vocabulary(self) -> None:
        content = load_content(CONTENT_ROOT)

        self.assertTrue(IMPLEMENTED_EFFECT_IDS.issubset(content.effects))
        self.assertTrue(IMPLEMENTED_PASSIVE_IDS.issubset(content.passives))
        self.assertTrue(IMPLEMENTED_SELECTOR_IDS.issubset(content.selectors))
        self.assertTrue(IMPLEMENTED_RESTRICTION_TYPE_IDS.issubset(content.restriction_types))

    def test_implemented_capabilities_match_their_resolver_dispatches(self) -> None:
        self.assertEqual(IMPLEMENTED_EFFECT_IDS, ACTION_EFFECT_DISPATCH_IDS)
        self.assertEqual(IMPLEMENTED_PASSIVE_IDS, PASSIVE_DISPATCH_IDS)
        self.assertEqual(IMPLEMENTED_SELECTOR_IDS, TARGET_SELECTOR_DISPATCH_IDS)
        self.assertEqual(IMPLEMENTED_RESTRICTION_TYPE_IDS, ACTION_RESTRICTION_DISPATCH_IDS)


if __name__ == "__main__":
    unittest.main()

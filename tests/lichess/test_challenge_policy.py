"""Audited upstream extension-point restrictions: exact startpos and bot-only."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "build/vendor/lichess-bot"
OVERLAY = ROOT / "deploy/lichess/extra_game_handlers.py"

class ChallengePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (BRIDGE / "lib/model.py").is_file():
            raise RuntimeError("pinned upstream checkout is required for challenge policy tests")
        sys.path.insert(0, str(BRIDGE))
        spec = importlib.util.spec_from_file_location("allfather_offline_handlers", OVERLAY)
        cls.overlay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.overlay)
    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(BRIDGE))

    def base(self, **overrides):
        value = dict(variant="standard", initial_fen="startpos", speed="rapid",
                     base=600, increment=5, rated=False,
                     challenger=SimpleNamespace(is_bot=True, name="approved_test_bot"))
        value.update(overrides)
        return SimpleNamespace(**value)
    def test_accepted_only_exact_offline_bot_challenge(self):
        self.assertTrue(self.overlay.is_supported_extra(self.base()))
        for update in (
            {"variant": "chess960"},
            {"initial_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
            {"speed": "blitz"},
            {"base": 300},
            {"increment": 0},
            {"rated": True},
            {"challenger": SimpleNamespace(is_bot=False, name="approved_test_bot")},
            {"challenger": SimpleNamespace(is_bot=True, name="other_test_bot")},
        ):
            with self.subTest(update=update):
                self.assertFalse(self.overlay.is_supported_extra(self.base(**update)))
    def test_overlay_cannot_select_game_options(self):
        self.assertEqual(self.overlay.game_specific_options(self.base()), {})

if __name__ == "__main__":
    unittest.main()

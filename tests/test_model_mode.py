import unittest

from jarvis_core.model_mode import ModelModeController


class ModelModeTests(unittest.TestCase):
    def setUp(self):
        self.mode = ModelModeController(
            fast_model="modelo-rapido",
            quality_model="modelo-profundo",
        )

    def test_deep_mode_can_be_enabled_and_disabled_in_portuguese(self):
        self.assertEqual(self.mode.active_model(), "modelo-rapido")
        enabled = self.mode.handle("Ative o modo profundo")
        self.assertIn("mais de um minuto", enabled)
        self.assertEqual(self.mode.active_model(), "modelo-profundo")

        disabled = self.mode.handle("Volte ao modo rápido")
        self.assertIn("Modo rápido ativado", disabled)
        self.assertEqual(self.mode.active_model(), "modelo-rapido")

    def test_unrelated_sentence_is_not_a_mode_command(self):
        self.assertIsNone(self.mode.handle("Explique o que é aprendizado profundo"))


if __name__ == "__main__":
    unittest.main()

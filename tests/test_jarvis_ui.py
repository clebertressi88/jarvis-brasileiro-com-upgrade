import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from jarvis_ui.jarvis_ui import JarvisUI


class JarvisUITests(unittest.TestCase):
    def test_floating_mode_uses_bottom_right_work_area(self):
        ui = JarvisUI()
        with patch.object(ui, "_work_area", return_value=(0, 0, 1920, 1040)):
            self.assertEqual(ui._window_position("floating"), (1582, 522, 320, 500))

    def test_original_mode_is_centered_at_original_size(self):
        ui = JarvisUI(width=400, height=700)
        with patch.object(ui, "_work_area", return_value=(0, 0, 1920, 1040)):
            self.assertEqual(ui._window_position("original"), (760, 170, 400, 700))

    def test_mode_switch_resizes_and_moves_window(self):
        ui = JarvisUI()
        ui.window = Mock()
        ui.floating_window = Mock()
        with patch.object(ui, "_window_position", return_value=(10, 20, 320, 500)):
            result = ui.set_window_mode("floating")

        ui.window.resize.assert_called_once_with(320, 500)
        ui.window.move.assert_called_once_with(10, 20)
        ui.window.hide.assert_called_once_with()
        ui.floating_window.show.assert_called_once_with()
        self.assertEqual(result["mode"], "floating")

    def test_original_mode_hides_overlay_and_shows_full_window(self):
        ui = JarvisUI()
        ui.window = Mock()
        ui.floating_window = Mock()
        ui._page_loaded = True
        with patch.object(ui, "_window_position", return_value=(10, 20, 400, 700)):
            result = ui.set_window_mode("original")

        ui.floating_window.hide.assert_called_once_with()
        ui.window.evaluate_js.assert_called_once_with("applyWindowMode('original')")
        ui.window.show.assert_called_once_with()
        self.assertEqual(result["mode"], "original")

    def test_listening_state_is_forwarded_to_the_web_interface(self):
        ui = JarvisUI()
        ui.window = Mock()
        ui._page_loaded = True

        ui.set_listening(True)
        ui.set_listening(False)

        self.assertEqual(
            ui.window.evaluate_js.call_args_list,
            [call("setListening(true)"), call("setListening(false)")],
        )

    def test_listening_state_waits_until_page_is_loaded(self):
        ui = JarvisUI()
        ui.window = Mock()

        ui.set_listening(True)
        ui.window.evaluate_js.assert_not_called()

        ui._on_page_loaded()
        ui.window.evaluate_js.assert_called_once_with("setListening(true)")

    def test_floating_asset_and_restore_button_are_present(self):
        ui_root = Path(__file__).parents[1] / "jarvis_ui" / "ui"
        html = (ui_root / "index.html").read_text(encoding="utf-8")
        css = (ui_root / "index.css").read_text(encoding="utf-8")

        self.assertIn("assets/jarvis-silhouette.png", html)
        self.assertIn('id="window-mode-toggle"', html)
        self.assertIn('id="voice-icon"', html)
        self.assertIn("OUVINDO", html)
        self.assertIn("body.mode-floating", css)
        self.assertIn("background-color: transparent !important", css)
        self.assertIn("background-image: none !important", css)
        self.assertTrue((ui_root / "assets" / "jarvis-silhouette.png").is_file())


if __name__ == "__main__":
    unittest.main()

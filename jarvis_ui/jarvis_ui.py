import threading
import webview
import logging
import json
import ctypes
import os
from pathlib import Path

from .floating_window import FloatingJarvisWindow

logger = logging.getLogger(__name__)


class Api:
    """Small bridge used by the HTML interface."""

    def __init__(self, ui):
        self.ui = ui

    def set_window_mode(self, mode):
        return self.ui.set_window_mode(mode)


class JarvisUI:
    def __init__(self, width=400, height=700, html_path="./ui/index.html", shutdown_event: threading.Event = None):
        self.html_path = html_path
        self.width = width
        self.height = height
        self.floating_width = 320
        self.floating_height = 500
        self.floating_margin = 18
        self.window = None
        self.floating_window = None
        self._page_loaded = False
        self._listening = False
        self.voice_finished = False
        self.muted = False
        self.shutdown_event = shutdown_event

        self.api = Api(self)

    @staticmethod
    def _work_area():
        """Return the usable desktop area, excluding the Windows taskbar."""
        if os.name == "nt":
            class Rect(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = Rect()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom

        return 0, 0, 1920, 1080

    def _window_position(self, mode):
        left, top, right, bottom = self._work_area()
        if mode == "floating":
            x = max(left, right - self.floating_width - self.floating_margin)
            y = max(top, bottom - self.floating_height - self.floating_margin)
            return x, y, self.floating_width, self.floating_height

        x = left + max(0, (right - left - self.width) // 2)
        y = top + max(0, (bottom - top - self.height) // 2)
        return x, y, self.width, self.height

    def set_window_mode(self, mode):
        if mode not in {"floating", "original"}:
            raise ValueError("Modo de janela invalido.")
        if self.window is None:
            return {"mode": mode}

        x, y, width, height = self._window_position(mode)
        self.window.resize(width, height)
        self.window.move(x, y)
        if mode == "floating":
            self.window.hide()
            if self.floating_window is not None:
                self.floating_window.show()
        else:
            if self.floating_window is not None:
                self.floating_window.hide()
            if self._page_loaded:
                self.window.evaluate_js("applyWindowMode('original')")
            self.window.show()
        return {"mode": mode, "x": x, "y": y, "width": width, "height": height}

    def print_message(self, isUser, message):
        if self.window is None or not self._page_loaded:
            return
        serialized_message = json.dumps(str(message), ensure_ascii=False)
        js_boolean = 'true' if isUser else 'false'
        self.window.evaluate_js(
            f"displayLine({js_boolean}, {serialized_message})")

    def showLoader(self, show):
        if self.window is None or not self._page_loaded:
            return
        js_boolean = 'true' if show else 'false'
        self.window.evaluate_js(f"displayLoader({js_boolean})")

    def set_listening(self, listening):
        self._listening = bool(listening)
        if self.floating_window is not None:
            self.floating_window.set_listening(self._listening)
        if self.window is None or not self._page_loaded:
            return
        js_boolean = 'true' if self._listening else 'false'
        self.window.evaluate_js(f"setListening({js_boolean})")

    def _on_page_loaded(self):
        self._page_loaded = True
        self.set_listening(self._listening)

    def _start_floating_window(self):
        assets = Path(self.html_path).resolve().parent / "assets"
        self.floating_window = FloatingJarvisWindow(
            image_path=assets / "jarvis-silhouette.png",
            mic_path=assets / "mic.png",
            geometry=self._window_position("floating"),
            on_restore=lambda: self.set_window_mode("original"),
        )
        self.floating_window.start()
        self.floating_window.set_listening(self._listening)

    def cleanup(self):
        logger.info("Shutdown: UI window closed, setting shutdown event...")
        if self.floating_window is not None:
            self.floating_window.stop()
        if self.shutdown_event is not None:
            self.shutdown_event.set()
        logger.info("Shutdown: shutdown event set.")

    def start(self):
        x, y, width, height = self._window_position("floating")
        self.window = webview.create_window(
            'J.A.R.V.I.S.', url=self.html_path, js_api=self.api,
            width=width,
            height=height,
            x=x,
            y=y,
            frameless=True,
            easy_drag=False,
            shadow=False,
            on_top=True,
            hidden=True,
            transparent=False,
            background_color="#000000",
        )
        logger.info("UI Started window.")
        self.window.events.loaded += self._on_page_loaded
        self.window.events.closed += self.cleanup
        webview.start(self._start_floating_window, debug=False, gui="edgechromium")


if __name__ == '__main__':
    ui = JarvisUI()
    ui.start()

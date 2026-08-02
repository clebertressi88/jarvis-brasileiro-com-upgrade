from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

from PIL import Image, ImageTk


logger = logging.getLogger(__name__)


class FloatingJarvisWindow:
    """Small native overlay whose chroma background is removed by Windows."""

    # Near-black is unlikely to occur as an exact RGB value in the artwork and
    # keeps semi-transparent antialiased edge pixels visually unobtrusive.
    CHROMA_KEY = "#010203"

    def __init__(
        self,
        *,
        image_path: Path,
        mic_path: Path,
        geometry: tuple[int, int, int, int],
        on_restore: Callable[[], None],
    ) -> None:
        self.image_path = image_path
        self.mic_path = mic_path
        self.geometry = geometry
        self.on_restore = on_restore
        self._commands: queue.Queue[tuple[str, bool | None]] = queue.Queue()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._root: tk.Tk | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="jarvis-floating-window",
        )
        self._thread.start()
        if not self._ready.wait(5):
            logger.warning("The native floating Jarvis window did not become ready.")

    def show(self) -> None:
        self._commands.put(("show", None))

    def hide(self) -> None:
        self._commands.put(("hide", None))

    def set_listening(self, listening: bool) -> None:
        self._commands.put(("listening", bool(listening)))

    def stop(self) -> None:
        self._commands.put(("stop", None))

    def _run(self) -> None:
        try:
            x, y, width, height = self.geometry
            root = tk.Tk()
            self._root = root
            root.title("J.A.R.V.I.S. Floating")
            root.overrideredirect(True)
            root.configure(background=self.CHROMA_KEY)
            root.geometry(f"{width}x{height}+{x}+{y}")
            root.attributes("-topmost", True)
            root.attributes("-transparentcolor", self.CHROMA_KEY)

            canvas = tk.Canvas(
                root,
                width=width,
                height=height,
                background=self.CHROMA_KEY,
                borderwidth=0,
                highlightthickness=0,
            )
            self._canvas = canvas
            canvas.pack(fill="both", expand=True)

            silhouette = self._load_image(
                self.image_path,
                max_width=width - 10,
                max_height=height - 8,
            )
            self._silhouette_photo = ImageTk.PhotoImage(silhouette)
            canvas.create_image(
                width // 2,
                height,
                image=self._silhouette_photo,
                anchor="s",
            )

            microphone = self._load_image(
                self.mic_path,
                max_width=54,
                max_height=54,
            )
            self._microphone_photo = ImageTk.PhotoImage(microphone)
            self._microphone_item = canvas.create_image(
                width // 2,
                int(height * 0.57),
                image=self._microphone_photo,
                state="hidden",
            )
            self._listening_item = canvas.create_text(
                width // 2,
                int(height * 0.57) + 40,
                text="OUVINDO",
                fill="#7defff",
                font=("Arial", 9, "bold"),
                state="hidden",
            )

            restore_x = width - 26
            canvas.create_oval(
                restore_x - 17,
                9,
                restore_x + 17,
                43,
                fill="#071a28",
                outline="#55e1ff",
                width=1,
                tags=("restore",),
            )
            canvas.create_text(
                restore_x,
                26,
                text="↗",
                fill="#c9f8ff",
                font=("Arial", 13, "bold"),
                tags=("restore",),
            )
            canvas.tag_bind(
                "restore",
                "<Button-1>",
                lambda _event: self._restore_original(),
            )

            drag = {"x": 0, "y": 0}

            def begin_drag(event) -> None:
                drag["x"] = event.x_root - root.winfo_x()
                drag["y"] = event.y_root - root.winfo_y()

            def move_window(event) -> None:
                new_x = event.x_root - drag["x"]
                new_y = event.y_root - drag["y"]
                root.geometry(f"+{new_x}+{new_y}")

            canvas.bind("<ButtonPress-1>", begin_drag)
            canvas.bind("<B1-Motion>", move_window)
            root.after(30, self._poll_commands)
            self._ready.set()
            root.mainloop()
        except Exception:
            logger.exception("The native floating Jarvis window failed.")
            self._ready.set()

    @staticmethod
    def _load_image(path: Path, *, max_width: int, max_height: int) -> Image.Image:
        image = Image.open(path).convert("RGBA")
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        return image

    def _restore_original(self) -> None:
        self.hide()
        threading.Thread(
            target=self.on_restore,
            daemon=True,
            name="jarvis-show-original",
        ).start()

    def _poll_commands(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            while True:
                command, value = self._commands.get_nowait()
                if command == "show":
                    root.deiconify()
                    root.attributes("-topmost", True)
                elif command == "hide":
                    root.withdraw()
                elif command == "listening":
                    state = "normal" if value else "hidden"
                    self._canvas.itemconfigure(self._microphone_item, state=state)
                    self._canvas.itemconfigure(
                        self._listening_item,
                        state=state,
                    )
                elif command == "stop":
                    root.destroy()
                    return
        except queue.Empty:
            pass
        except Exception:
            logger.exception("Could not update the floating Jarvis window.")
        root.after(30, self._poll_commands)

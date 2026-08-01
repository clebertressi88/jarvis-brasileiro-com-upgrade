import asyncio
import logging
import os
import threading
from jarvis_llm.jarvis_llm import chat_with_jarvis
from jarvis_installer import InstallerAgent
from jarvis_programmer import ProgrammerAgent
from jarvis_tools import ComputerCommandRouter
from jarvis_memory import get_local_memory
from jarvis_core import SafeActionCoordinator

from utils.args import (
    parse_arguments,
    InputMode,
    OutputMode,
    OutputInterface,
    PushToTalk,
)
from utils.logging_config import setup_logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

shutdown_event = threading.Event()


def resource_path(rel_path: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / rel_path)


def run_jarvis_logic(
    input_voice_enabled,
    output_voice_enabled,
    ui_enabled,
    push_to_talk_enabled,
    jarvis_ui,
    jarvis_tts,
    jarvis_stt,
    action_coordinator,
):
    async def handle_conversation():
        print("JARVIS: Initialized.")
        try:
            while not shutdown_event.is_set():
                if not input_voice_enabled:
                    user_input = input("You: ")
                else:
                    user_input = jarvis_stt.transcribe_user_audio(
                        push_to_talk=push_to_talk_enabled
                    )

                if shutdown_event.is_set():
                    break

                if not user_input or not user_input.strip():
                    continue

                if ui_enabled and jarvis_ui:
                    jarvis_ui.print_message(isUser=True, message=user_input)
                elif (not ui_enabled) and input_voice_enabled:
                    print(f"You: {user_input}")

                if "bye" in user_input.lower() and (not ui_enabled):
                    print("JARVIS: Shutting down. Goodbye!")
                    shutdown_event.set()
                    break

                command_response = action_coordinator.handle(user_input)
                if command_response is not None:
                    if ui_enabled:
                        jarvis_ui.print_message(
                            isUser=False, message=command_response
                        )
                    else:
                        print(f"JARVIS: {command_response}")

                    if output_voice_enabled:
                        jarvis_tts.speak(command_response)
                    continue

                async for sentence in chat_with_jarvis(user_input):
                    if shutdown_event.is_set():
                        break
                    if ui_enabled:
                        jarvis_ui.print_message(isUser=False, message=sentence)
                    elif not ui_enabled:
                        print(f"JARVIS: {sentence}")

                    if output_voice_enabled and not sentence.startswith("Fontes consultadas:"):
                        jarvis_tts.speak(sentence)

        except (KeyboardInterrupt, EOFError):
            logger.info("Shutdown: conversation loop interrupted.")
            shutdown_event.set()
        finally:
            logger.info("Shutdown: conversation loop exited, stopping STT...")
            if jarvis_stt:
                jarvis_stt.stop()
            logger.info("Shutdown: STT stopped. Stopping TTS...")
            if jarvis_tts:
                jarvis_tts.stop()
            logger.info("Shutdown: TTS stopped.")

    try:
        asyncio.run(handle_conversation())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown: async loop interrupted.")
        shutdown_event.set()


def main():
    args = parse_arguments()
    setup_logging(args.log.name)

    from jarvis_tts.jarvis_tts import JarvisTTS
    from jarvis_stt.jarvis_stt import JarvisSTT
    from jarvis_ui.jarvis_ui import JarvisUI

    input_voice_enabled = args.input == InputMode.VOICE
    output_voice_enabled = args.output == OutputMode.VOICE
    ui_enabled = args.interface == OutputInterface.UI
    push_to_talk_enabled = args.push_to_talk == PushToTalk.ON

    logger.info("Starting JARVIS with the following configuration:")
    logger.info(f"Input Mode: {args.input.name}")
    logger.info(f"Output Mode: {args.output.name}")
    logger.info(f"Output Interface: {args.interface.name}")
    logger.info(
        f"Push to Talk: {args.push_to_talk.name if args.push_to_talk else 'N/A'}"
    )

    jarvis_ui = None
    jarvis_tts = None
    jarvis_stt = None
    programmer_agent = ProgrammerAgent()
    try:
        installer_agent = InstallerAgent()
    except Exception as exc:
        logger.warning("Installer agent unavailable: %s", exc)
        installer_agent = None
    command_router = ComputerCommandRouter()
    local_memory = get_local_memory()
    action_coordinator = SafeActionCoordinator(
        memory=local_memory,
        programmer=programmer_agent,
        installer=installer_agent,
        computer=command_router,
    )

    if output_voice_enabled:
        jarvis_tts = JarvisTTS(
            speaker_sample=os.environ.get("JARVIS_VOICE_SAMPLE", "rafael"),
            ui_enabled=ui_enabled,
            shutdown_event=shutdown_event
        )

    if input_voice_enabled:
        jarvis_stt = JarvisSTT(shutdown_event=shutdown_event)

    if ui_enabled:
        jarvis_ui = JarvisUI(
            html_path=resource_path("jarvis_ui/ui/index.html"),
            shutdown_event=shutdown_event,
        )
        if jarvis_stt:
            jarvis_stt.set_listening_callback(jarvis_ui.set_listening)
        logic_thread = threading.Thread(
            target=run_jarvis_logic,
            args=(
                input_voice_enabled,
                output_voice_enabled,
                ui_enabled,
                push_to_talk_enabled,
                jarvis_ui,
                jarvis_tts,
                jarvis_stt,
                action_coordinator,
            ),
            daemon=True,
            name="jarvis-logic",
        )
        logic_thread.start()

        # Block main thread on the UI — webview requires the main thread
        jarvis_ui.start()

        # webview.start() has returned, meaning the window is closed.
        # shutdown_event is already set by cleanup(). Just wait for logic thread.
        logger.info("Shutdown: waiting for logic thread to finish...")
        logic_thread.join(timeout=5)
        if logic_thread.is_alive():
            logger.warning("Shutdown: logic thread did not exit within timeout.")
        else:
            logger.info("Shutdown: logic thread finished.")

        logger.info("Shutdown: complete.")
    else:
        run_jarvis_logic(
            input_voice_enabled,
            output_voice_enabled,
            ui_enabled,
            push_to_talk_enabled,
            jarvis_ui,
            jarvis_tts,
            jarvis_stt,
            action_coordinator,
        )
        logger.info("Shutdown: complete.")


if __name__ == "__main__":
    main()

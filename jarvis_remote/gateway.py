from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import secrets
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .security import PairingManager, PairingStore


logger = logging.getLogger(__name__)
MAX_FRAME_BYTES = 16 * 1024
MAX_MESSAGE_CHARACTERS = 4_000
MAX_MESSAGES_PER_MINUTE = 20
AUTH_TIMEOUT_SECONDS = 30.0


async def jarvis_responder(text: str) -> AsyncIterator[str]:
    """Answer requests that were not handled as constrained local actions."""
    from jarvis_llm.jarvis_llm import chat_with_jarvis

    async for sentence in chat_with_jarvis(text):
        if sentence:
            yield str(sentence)


class JarvisRemoteGateway:
    def __init__(
        self,
        pairing: PairingManager,
        *,
        responder: Callable[[str], AsyncIterator[str]] = jarvis_responder,
        action_handler: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        self.pairing = pairing
        self.responder = responder
        self.action_handler = action_handler
        self._conversation_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

    async def handle(self, websocket: ServerConnection) -> None:
        challenge = secrets.token_urlsafe(24)
        await self._send(websocket, {"type": "challenge", "nonce": challenge})

        try:
            raw_auth = await asyncio.wait_for(
                websocket.recv(decode=True), timeout=AUTH_TIMEOUT_SECONDS
            )
            auth = self._parse_object(raw_auth)
            authenticated = await self._authenticate(websocket, auth, challenge)
            if not authenticated:
                await websocket.close(code=1008, reason="authentication failed")
                return

            request_times: deque[float] = deque()
            async for raw_message in websocket:
                payload = self._parse_object(raw_message)
                if payload.get("type") != "message":
                    await self._send_error(websocket, None, "tipo de mensagem inválido")
                    continue

                message_id = payload.get("id")
                text = payload.get("text")
                if not self._valid_message_id(message_id):
                    await self._send_error(websocket, None, "identificador de mensagem inválido")
                    continue
                if not isinstance(text, str) or not text.strip():
                    await self._send_error(websocket, message_id, "mensagem vazia")
                    continue
                text = text.strip()
                if len(text) > MAX_MESSAGE_CHARACTERS:
                    await self._send_error(websocket, message_id, "mensagem muito longa")
                    continue
                if not self._within_rate_limit(request_times):
                    await self._send_error(websocket, message_id, "limite temporário atingido")
                    continue

                action_response = None
                if self.action_handler is not None:
                    async with self._action_lock:
                        action_response = await self.action_handler(text)

                if action_response is not None:
                    for offset in range(0, len(action_response), 2_000):
                        await self._send(
                            websocket,
                            {
                                "type": "chunk",
                                "id": message_id,
                                "text": action_response[offset : offset + 2_000],
                            },
                        )
                    await self._send(websocket, {"type": "done", "id": message_id})
                    continue

                async with self._conversation_lock:
                    try:
                        async for chunk in self.responder(text):
                            await self._send(
                                websocket,
                                {"type": "chunk", "id": message_id, "text": chunk},
                            )
                        await self._send(websocket, {"type": "done", "id": message_id})
                    except Exception:
                        logger.exception("Remote Jarvis response failed")
                        await self._send_error(
                            websocket,
                            message_id,
                            "não consegui obter uma resposta do Jarvis",
                        )
        except (asyncio.TimeoutError, ConnectionClosed):
            return
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.info("Rejected malformed remote frame: %s", exc)
            try:
                await websocket.close(code=1007, reason="invalid frame")
            except ConnectionClosed:
                pass

    async def _authenticate(
        self,
        websocket: ServerConnection,
        payload: dict,
        challenge: str,
    ) -> bool:
        message_type = payload.get("type")
        client_id = payload.get("client_id")
        if not isinstance(client_id, str):
            return False

        if message_type == "pair":
            code = payload.get("code")
            if not isinstance(code, str):
                return False
            try:
                secret = self.pairing.pair(client_id, code)
            except (PermissionError, ValueError):
                await self._send_error(websocket, None, "pareamento recusado")
                return False
            await self._send(
                websocket,
                {"type": "paired", "client_id": client_id, "secret": secret},
            )
            await self._send_ready(websocket)
            return True

        if message_type == "auth":
            proof = payload.get("proof")
            if not isinstance(proof, str):
                return False
            if not self.pairing.verify(client_id, challenge, proof):
                await self._send_error(websocket, None, "autenticação recusada")
                return False
            await self._send_ready(websocket)
            return True
        return False

    async def _send_ready(self, websocket: ServerConnection) -> None:
        capabilities = ["conversation"]
        if self.action_handler is not None:
            capabilities.extend(["computer_actions", "local_confirmation"])
        await self._send(
            websocket,
            {"type": "ready", "protocol": 2, "capabilities": capabilities},
        )

    @staticmethod
    def _parse_object(raw: str | bytes) -> dict:
        if isinstance(raw, bytes):
            if len(raw) > MAX_FRAME_BYTES:
                raise ValueError("frame muito grande")
            raw = raw.decode("utf-8")
        if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ValueError("frame muito grande")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("o frame precisa ser um objeto JSON")
        return payload

    @staticmethod
    def _valid_message_id(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return str(uuid.UUID(value)) == value.lower()
        except ValueError:
            return False

    @staticmethod
    def _within_rate_limit(request_times: deque[float]) -> bool:
        now = time.monotonic()
        while request_times and now - request_times[0] > 60.0:
            request_times.popleft()
        if len(request_times) >= MAX_MESSAGES_PER_MINUTE:
            return False
        request_times.append(now)
        return True

    @staticmethod
    async def _send(websocket: ServerConnection, payload: dict) -> None:
        await websocket.send(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    async def _send_error(
        self, websocket: ServerConnection, message_id: str | None, detail: str
    ) -> None:
        await self._send(
            websocket,
            {"type": "error", "id": message_id, "message": detail},
        )


def _loopback_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use um endereço IP de loopback") from exc
    if not address.is_loopback:
        raise argparse.ArgumentTypeError(
            "o gateway aceita somente loopback; use o Tailscale Serve para acesso remoto"
        )
    return value


async def run_gateway(
    gateway: JarvisRemoteGateway,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    async with serve(
        gateway.handle,
        host,
        port,
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
        compression=None,
        server_header=None,
    ):
        logger.info("Jarvis Remote listening only on %s:%d", host, port)
        await asyncio.Future()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gateway privado de conversa entre o Android e o Jarvis local."
    )
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--pair",
        action="store_true",
        help="gera um código temporário para o primeiro celular",
    )
    parser.add_argument(
        "--replace-pairing",
        action="store_true",
        help="substitui o celular pareado após confirmação local",
    )
    parser.add_argument("--state", type=Path, default=None, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("A porta precisa estar entre 1024 e 65535.")
    if args.pair and args.replace_pairing:
        raise SystemExit("Use apenas uma opção de pareamento por vez.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = PairingStore(args.state)
    pairing = PairingManager(store)

    should_pair = args.pair or not store.exists()
    replace_existing = False
    if args.replace_pairing:
        confirmation = input(
            "Isso invalidará o celular atual. Digite RECONFIGURAR para continuar: "
        ).strip()
        if confirmation != "RECONFIGURAR":
            raise SystemExit("Pareamento mantido; nenhuma alteração foi feita.")
        should_pair = True
        replace_existing = True

    if should_pair:
        try:
            code = pairing.start_pairing(replace_existing=replace_existing)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        print("\nCódigo de pareamento do Jarvis Remote:", code)
        print("Ele vale por 5 minutos e aceita no máximo 5 tentativas.\n")

    print(f"Gateway local: ws://{args.host}:{args.port}")
    print(
        "Para acesso privado, configure em outro PowerShell: "
        f"tailscale serve --bg {args.port}"
    )
    print("Nunca use Tailscale Funnel para este gateway.\n")
    action_handler = None
    try:
        from .actions import build_remote_action_executor

        action_handler = build_remote_action_executor().handle
    except Exception:
        logger.exception("Remote computer actions are unavailable")

    asyncio.run(
        run_gateway(
            JarvisRemoteGateway(pairing, action_handler=action_handler),
            host=args.host,
            port=args.port,
        )
    )


if __name__ == "__main__":
    main()

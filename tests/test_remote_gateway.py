import argparse
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from jarvis_remote.gateway import JarvisRemoteGateway, _loopback_host
from jarvis_remote.security import PairingManager, PairingRecord, PairingStore


class RemotePairingTests(unittest.TestCase):
    def test_gateway_refuses_lan_binding(self):
        self.assertEqual(_loopback_host("127.0.0.1"), "127.0.0.1")
        with self.assertRaises(argparse.ArgumentTypeError):
            _loopback_host("0.0.0.0")

    def test_pairing_store_round_trip_and_client_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PairingStore(Path(directory) / "paired.json")
            record = PairingRecord(
                client_id="12345678-1234-1234-1234-123456789abc",
                secret=b"a" * 32,
            )
            store.save(record)

            self.assertEqual(store.load(), record)
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn((b"a" * 32).decode("ascii"), raw)

    def test_pairing_code_is_one_time_and_proof_is_client_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PairingManager(PairingStore(Path(directory) / "paired.json"))
            client_id = "12345678-1234-1234-1234-123456789abc"
            code = manager.start_pairing()
            encoded_secret = manager.pair(client_id, code)
            secret = manager.decode_secret(encoded_secret)
            challenge = "challenge-value"
            proof = manager.proof(secret, client_id, challenge)

            self.assertTrue(manager.verify(client_id, challenge, proof))
            self.assertFalse(
                manager.verify(
                    "87654321-4321-4321-4321-cba987654321",
                    challenge,
                    proof,
                )
            )
            with self.assertRaises(PermissionError):
                manager.pair(client_id, code)


class RemoteGatewayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_authenticate_and_exchange_private_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PairingManager(PairingStore(Path(directory) / "paired.json"))
            code = manager.start_pairing()
            client_id = "12345678-1234-1234-1234-123456789abc"

            async def responder(text):
                yield f"Resposta privada: {text}"

            gateway = JarvisRemoteGateway(manager, responder=responder)
            async with serve(gateway.handle, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                uri = f"ws://127.0.0.1:{port}"

                async with connect(uri) as websocket:
                    challenge = json.loads(await websocket.recv())
                    self.assertEqual(challenge["type"], "challenge")
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "pair",
                                "client_id": client_id,
                                "code": code,
                            }
                        )
                    )
                    paired = json.loads(await websocket.recv())
                    ready = json.loads(await websocket.recv())
                    self.assertEqual(paired["type"], "paired")
                    self.assertEqual(ready["type"], "ready")

                    message_id = str(uuid.uuid4())
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "message",
                                "id": message_id,
                                "text": "Olá Jarvis",
                            }
                        )
                    )
                    chunk = json.loads(await websocket.recv())
                    done = json.loads(await websocket.recv())
                    self.assertEqual(chunk["text"], "Resposta privada: Olá Jarvis")
                    self.assertEqual(done, {"type": "done", "id": message_id})

                secret = manager.decode_secret(paired["secret"])
                async with connect(uri) as websocket:
                    challenge = json.loads(await websocket.recv())
                    proof = manager.proof(secret, client_id, challenge["nonce"])
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "auth",
                                "client_id": client_id,
                                "proof": proof,
                            }
                        )
                    )
                    ready = json.loads(await websocket.recv())
                    self.assertEqual(ready["type"], "ready")

    async def test_wrong_client_cannot_authenticate(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PairingManager(PairingStore(Path(directory) / "paired.json"))
            code = manager.start_pairing()
            client_id = "12345678-1234-1234-1234-123456789abc"
            secret = manager.decode_secret(manager.pair(client_id, code))

            async def responder(_text):
                yield "não deve ser chamado"

            gateway = JarvisRemoteGateway(manager, responder=responder)
            async with serve(gateway.handle, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as websocket:
                    challenge = json.loads(await websocket.recv())
                    attacker = "87654321-4321-4321-4321-cba987654321"
                    proof = manager.proof(secret, attacker, challenge["nonce"])
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "auth",
                                "client_id": attacker,
                                "proof": proof,
                            }
                        )
                    )
                    error = json.loads(await websocket.recv())
                    self.assertEqual(error["type"], "error")
                    self.assertEqual(error["message"], "autenticação recusada")

    async def test_authenticated_action_uses_local_action_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PairingManager(PairingStore(Path(directory) / "paired.json"))
            code = manager.start_pairing()
            client_id = "12345678-1234-1234-1234-123456789abc"

            async def responder(_text):
                yield "conversa não deveria ser usada"

            async def action_handler(text):
                return "Abri a Calculadora." if text == "Abra a calculadora" else None

            gateway = JarvisRemoteGateway(
                manager,
                responder=responder,
                action_handler=action_handler,
            )
            async with serve(gateway.handle, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as websocket:
                    challenge = json.loads(await websocket.recv())
                    self.assertEqual(challenge["type"], "challenge")
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "pair",
                                "client_id": client_id,
                                "code": code,
                            }
                        )
                    )
                    await websocket.recv()
                    ready = json.loads(await websocket.recv())
                    self.assertIn("computer_actions", ready["capabilities"])

                    message_id = str(uuid.uuid4())
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "message",
                                "id": message_id,
                                "text": "Abra a calculadora",
                            }
                        )
                    )
                    chunk = json.loads(await websocket.recv())
                    done = json.loads(await websocket.recv())
                    self.assertEqual(chunk["text"], "Abri a Calculadora.")
                    self.assertEqual(done, {"type": "done", "id": message_id})

import tempfile
import unittest
from pathlib import Path

from jarvis_memory import LocalMemory


def deterministic_embeddings(texts):
    vectors = []
    for text in texts:
        plain = text.lower()
        vectors.append(
            [
                float(plain.count("python")),
                float(plain.count("cafe") + plain.count("café")),
                float(plain.count("projeto")),
                float(plain.count("editor") + plain.count("vs code")),
                1.0,
            ]
        )
    return vectors


class LocalMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.documents = self.root / "Documents"
        self.documents.mkdir()
        self.memory = LocalMemory(
            database_path=self.root / "memory.sqlite3",
            allowed_roots=[self.documents],
            embedding_provider=deterministic_embeddings,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_explicit_memory_is_persistent_and_semantic(self):
        response = self.memory.handle("Lembre que meu projeto principal usa Python")
        self.assertIn("memória local", response)

        reopened = LocalMemory(
            database_path=self.root / "memory.sqlite3",
            allowed_roots=[self.documents],
            embedding_provider=deterministic_embeddings,
        )
        recalled = reopened.recall("Python", limit=3)
        self.assertEqual(recalled, ["meu projeto principal usa Python"])

    def test_clear_requires_exact_confirmation(self):
        self.memory.add_memory("prefiro café")
        request = self.memory.handle("Apague toda a memória")
        self.assertIn("confirmar esquecimento", request)

        refused = self.memory.handle("confirmar")
        self.assertIn("exatamente", refused)
        self.assertTrue(self.memory.recall("café"))

        cleared = self.memory.handle("confirmar esquecimento")
        self.assertIn("foram apagados", cleared)
        self.assertEqual(self.memory.recall("café"), [])

    def test_documents_are_indexed_only_after_confirmation(self):
        document = self.documents / "projeto.txt"
        document.write_text("O projeto Atlas foi criado em Python.", encoding="utf-8")

        request = self.memory.handle("Indexe meus documentos")
        self.assertIn("confirmar indexação", request)
        self.assertEqual(self.memory.search_documents("Python"), [])

        completed = self.memory.handle("confirmar indexação")
        self.assertIn("1 arquivos", completed)
        results = self.memory.search_documents("Python")
        self.assertEqual(results[0][0], "projeto.txt")
        self.assertIn("Atlas", results[0][1])

    def test_specific_memory_can_be_forgotten_after_confirmation(self):
        self.memory.add_memory("meu editor preferido é o VS Code")
        self.memory.add_memory("meu café preferido é espresso")

        request = self.memory.handle("Esqueça meu editor preferido")
        self.assertIn("VS Code", request)
        self.assertTrue(self.memory.recall("editor"))

        response = self.memory.handle("confirmar esquecimento")
        self.assertIn("1 lembrança", response)
        self.assertEqual(self.memory.recall("editor"), [])
        self.assertTrue(self.memory.recall("café"))

    def test_recent_conversation_uses_requested_window(self):
        for index in range(5):
            self.memory.record_exchange(f"pergunta {index}", f"resposta {index}")
        messages = self.memory.recent_messages(interactions=3)
        self.assertEqual(len(messages), 6)
        self.assertEqual(messages[0]["content"], "pergunta 2")
        self.assertEqual(messages[-1]["content"], "resposta 4")


if __name__ == "__main__":
    unittest.main()

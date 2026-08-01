import os
import tempfile
import unittest
from pathlib import Path

from jarvis_tools import ComputerCommandRouter


class ComputerCommandSecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.desktop = self.base / "Desktop"
        self.documents = self.base / "Documents"
        self.downloads = self.base / "Downloads"
        for folder in (self.desktop, self.documents, self.downloads):
            folder.mkdir()

        self.launched = []
        self.opened = []
        self.router = ComputerCommandRouter(
            allowed_roots={
                "area de trabalho": self.desktop,
                "documentos": self.documents,
                "downloads": self.downloads,
            },
            process_launcher=self.launched.append,
            file_opener=self.opened.append,
            key_sender=lambda _key: None,
            installed_program_provider=lambda: (),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_python_and_javascript_are_never_opened_by_file_association(self):
        for filename in ("rotina.py", "automacao.js"):
            with self.subTest(filename=filename):
                path = self.documents / filename
                path.write_text("raise SystemExit", encoding="utf-8")

                response = self.router.handle(
                    f"Abra o arquivo {filename} nos documentos"
                )

                self.assertIsNotNone(response)
                self.assertIn("não", response.lower())
                self.assertEqual(self.opened, [])

    def test_hard_link_is_rejected_instead_of_exposing_its_contents(self):
        outside = self.base / "segredo-fora.txt"
        outside.write_text("CONTEUDO_SECRETO_HARDLINK", encoding="utf-8")
        hard_link = self.documents / "atalho-hardlink.txt"
        try:
            os.link(outside, hard_link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard links não estão disponíveis neste ambiente: {exc}")

        response = self.router.handle(
            "Leia o arquivo atalho-hardlink.txt nos documentos"
        )

        self.assertIsNotNone(response)
        self.assertNotIn("CONTEUDO_SECRETO_HARDLINK", response)
        self.assertTrue(
            any(term in response.lower() for term in ("não", "bloque", "permit")),
            response,
        )

    def test_symbolic_link_is_rejected_even_when_target_is_allowed(self):
        target = self.documents / "arquivo-real.txt"
        target.write_text("CONTEUDO_SECRETO_SYMLINK", encoding="utf-8")
        symbolic_link = self.documents / "atalho-simbolico.txt"
        try:
            symbolic_link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"links simbólicos não estão disponíveis neste ambiente: {exc}")

        response = self.router.handle(
            "Leia o arquivo atalho-simbolico.txt nos documentos"
        )

        self.assertIsNotNone(response)
        self.assertNotIn("CONTEUDO_SECRETO_SYMLINK", response)
        self.assertTrue(
            any(term in response.lower() for term in ("não", "bloque", "permit")),
            response,
        )

    def test_punctuated_negative_reply_cancels_pending_deletion(self):
        path = self.documents / "preservar.txt"
        path.write_text("não apagar", encoding="utf-8")
        request = self.router.handle(
            "Apague o arquivo preservar.txt nos documentos"
        )
        self.assertIsNotNone(request)
        self.assertIsNotNone(self.router.pending_action)

        response = self.router.handle("não, cancele por favor")

        self.assertIsNotNone(response)
        self.assertIn("cancelada", response.lower())
        self.assertTrue(path.exists())
        self.assertIsNone(self.router.pending_action)

    def test_confirmation_revalidates_file_before_deleting_it(self):
        path = self.downloads / "alvo.txt"
        path.write_text("arquivo original", encoding="utf-8")
        request = self.router.handle(
            "Exclua o arquivo alvo.txt nos downloads"
        )
        self.assertIsNotNone(request)
        self.assertIsNotNone(self.router.pending_action)

        path.unlink()
        path.write_text("arquivo substituto", encoding="utf-8")

        response = self.router.handle("confirmar")

        self.assertIsNotNone(response)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "arquivo substituto")
        self.assertNotIn("apagado permanentemente", response.lower())
        self.assertIsNone(self.router.pending_action)

    def test_windows_reserved_device_names_are_blocked(self):
        reserved_names = (
            "CON",
            "aux.txt",
            "PRN.md",
            "NUL.json",
            "COM1.txt",
            "lpt9.csv",
        )

        for filename in reserved_names:
            with self.subTest(filename=filename):
                self.router.pending_action = None
                entries_before = {path.name for path in self.documents.iterdir()}
                response = self.router.handle(
                    f"Crie um arquivo chamado {filename} nos documentos "
                    "com o conteúdo teste"
                )

                self.assertIsNotNone(response)
                self.assertTrue(
                    any(
                        term in response.lower()
                        for term in ("reservado", "não permitido", "inválido")
                    ),
                    response,
                )
                self.assertIsNone(self.router.pending_action)
                self.assertEqual(
                    {path.name for path in self.documents.iterdir()},
                    entries_before,
                )


if __name__ == "__main__":
    unittest.main()

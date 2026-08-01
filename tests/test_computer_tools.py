import tempfile
import unittest
from pathlib import Path

from jarvis_tools import ComputerCommandRouter


class ComputerCommandRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.desktop = base / "Desktop"
        self.documents = base / "Documents"
        self.downloads = base / "Downloads"
        for folder in (self.desktop, self.documents, self.downloads):
            folder.mkdir()

        self.launched = []
        self.opened = []
        self.keys = []
        self.closed = []
        self.router = ComputerCommandRouter(
            allowed_roots={
                "area de trabalho": self.desktop,
                "documentos": self.documents,
                "downloads": self.downloads,
            },
            process_launcher=self.launched.append,
            process_closer=lambda names: self.closed.append(names) or True,
            file_opener=self.opened.append,
            key_sender=self.keys.append,
            installed_program_provider=lambda: (),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_read_and_append_text_file(self):
        response = self.router.handle(
            "Crie um arquivo chamado notas.txt nos documentos com o conteúdo Olá mundo"
        )
        path = self.documents / "notas.txt"
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), "Olá mundo")
        self.assertIn("Arquivo criado", response)

        response = self.router.handle("Adicione comprar café ao arquivo notas.txt nos documentos")
        self.assertIn("Conteúdo adicionado", response)
        self.assertEqual(
            path.read_text(encoding="utf-8"), "Olá mundo\ncomprar café"
        )

        response = self.router.handle("Leia o arquivo notas.txt nos documentos")
        self.assertIn("Olá mundo", response)
        self.assertIn("comprar café", response)

    def test_replace_requires_confirmation(self):
        path = self.documents / "notas.txt"
        path.write_text("antigo", encoding="utf-8")

        response = self.router.handle(
            "Substitua o conteúdo do arquivo notas.txt nos documentos por conteúdo novo"
        )
        self.assertIn("confirmar", response.lower())
        self.assertEqual(path.read_text(encoding="utf-8"), "antigo")

        response = self.router.handle("confirmar")
        self.assertIn("substituído", response)
        self.assertEqual(path.read_text(encoding="utf-8"), "conteúdo novo")

    def test_delete_requires_confirmation_and_can_be_cancelled(self):
        path = self.desktop / "rascunho.txt"
        path.write_text("teste", encoding="utf-8")

        response = self.router.handle(
            "Apague o arquivo rascunho.txt na área de trabalho"
        )
        self.assertIn("confirmar", response.lower())
        self.assertTrue(path.exists())

        response = self.router.handle("cancelar")
        self.assertIn("cancelada", response.lower())
        self.assertTrue(path.exists())

        self.router.handle("Apague o arquivo rascunho.txt na área de trabalho")
        response = self.router.handle("sim")
        self.assertIn("apagado", response.lower())
        self.assertFalse(path.exists())

    def test_rejects_directory_traversal(self):
        response = self.router.handle(
            "Crie um arquivo chamado ..\\segredo.txt nos documentos com o conteúdo teste"
        )
        self.assertIn("não permitidos", response)

    def test_unregistered_program_is_not_opened(self):
        response = self.router.handle("Abra a calculadora")
        self.assertIn("Calculadora", response)
        self.assertEqual(self.launched, [["calc.exe"]])

        response = self.router.handle("Abra o powershell")
        self.assertIn("Não encontrei", response)
        self.assertEqual(len(self.launched), 1)

        response = self.router.handle("Jarves, abra a calculadora.")
        self.assertIn("Calculadora", response)
        self.assertEqual(self.launched[-1], ["calc.exe"])

    def test_calculator_closes_directly_and_notepad_requires_confirmation(self):
        response = self.router.handle("Charves, feche a calculadora.")

        self.assertIn("Fechei a Calculadora", response)
        self.assertEqual(self.closed, [("CalculatorApp.exe", "Calculator.exe")])

        response = self.router.handle("Feche o bloco de notas")
        self.assertIn("confirmar ou cancelar", response)
        self.assertEqual(len(self.closed), 1)

        response = self.router.handle("confirmar")
        self.assertIn("Solicitei o fechamento", response)
        self.assertEqual(self.closed[-1], ("Notepad.exe",))

    def test_program_close_can_be_cancelled_and_explorer_is_protected(self):
        response = self.router.handle("Feche o Chrome")
        self.assertIn("trabalho não salvo", response)

        response = self.router.handle("cancelar")
        self.assertIn("cancelada", response.lower())
        self.assertEqual(self.closed, [])

        response = self.router.handle("Feche o explorador de arquivos")
        self.assertIn("interface do Windows", response)
        self.assertEqual(self.closed, [])

    def test_create_file_asks_location_and_uses_next_answer(self):
        response = self.router.handle(
            "Crie um arquivo chamado lista.txt com o conteúdo comprar café"
        )
        self.assertIn("Onde deseja salvar", response)
        self.assertIsNotNone(self.router.pending_save)
        self.assertFalse((self.documents / "lista.txt").exists())

        response = self.router.handle("Salve automaticamente em Downloads")
        path = self.downloads / "lista.txt"
        self.assertIn("Arquivo criado", response)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), "comprar café")
        self.assertIsNone(self.router.pending_save)

    def test_pending_save_repeats_options_and_can_be_cancelled(self):
        self.router.handle("Crie um arquivo chamado rascunho.md contendo teste")

        response = self.router.handle("em outra pasta")
        self.assertIn("Ainda preciso saber", response)
        self.assertIsNotNone(self.router.pending_save)

        response = self.router.handle("cancelar")
        self.assertIn("Salvamento cancelado", response)
        self.assertIsNone(self.router.pending_save)
        self.assertFalse((self.documents / "rascunho.md").exists())

    def test_media_commands_use_fixed_keys(self):
        response = self.router.handle("Aumente o volume")
        self.assertIn("Aumentei", response)
        self.assertEqual(self.keys, ["volume up", "volume up", "volume up"])

    def test_search_is_limited_to_allowed_roots(self):
        (self.downloads / "relatorio-julho.pdf").write_bytes(b"pdf")
        response = self.router.handle("Procure o arquivo relatório julho")
        self.assertIn("downloads", response)
        self.assertIn("relatorio-julho.pdf", response)


if __name__ == "__main__":
    unittest.main()

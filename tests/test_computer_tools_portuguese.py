import tempfile
import unittest
from pathlib import Path

from jarvis_tools import ComputerCommandRouter
from jarvis_tools.computer_tools import InstalledProgram


class PortugueseComputerCommandsTests(unittest.TestCase):
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
        self.router = ComputerCommandRouter(
            allowed_roots={
                "area de trabalho": self.desktop,
                "documentos": self.documents,
                "downloads": self.downloads,
            },
            process_launcher=self.launched.append,
            file_opener=self.opened.append,
            key_sender=self.keys.append,
            installed_program_provider=lambda: (),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_program_variations_with_accents_are_recognized(self):
        cases = (
            ("Abra o bloco de notas", ["notepad.exe"]),
            ("Inicie a calculadora", ["calc.exe"]),
            ("Executar o programa Paint", ["mspaint.exe"]),
            (
                "Abra a câmera",
                [
                    "explorer.exe",
                    r"shell:AppsFolder\Microsoft.WindowsCamera_8wekyb3d8bbwe!App",
                ],
            ),
        )

        for command, expected_launch in cases:
            with self.subTest(command=command):
                response = self.router.handle(command)
                self.assertIsNotNone(response)
                self.assertIn("Abrindo", response)
                self.assertEqual(self.launched[-1], expected_launch)

    def test_program_names_allow_natural_language_and_speech_errors(self):
        cases = (
            ("Jarvis, abre a calculadoura pra mim", ["calc.exe"]),
            ("Você pode iniciar o aplicativo de contas?", ["calc.exe"]),
            ("Pode abrir pra mim a calculadora?", ["calc.exe"]),
            ("Você consegue abrir o programa de calculo?", ["calc.exe"]),
            ("Roda o bloco di notas agora", ["notepad.exe"]),
            ("Abra minhas pastas por favor", ["explorer.exe", str(Path.home())]),
            (
                "Executa a web cam",
                [
                    "explorer.exe",
                    r"shell:AppsFolder\Microsoft.WindowsCamera_8wekyb3d8bbwe!App",
                ],
            ),
        )

        for command, expected_launch in cases:
            with self.subTest(command=command):
                response = self.router.handle(command)
                self.assertIsNotNone(response)
                self.assertIn("Abrindo", response)
                self.assertEqual(self.launched[-1], expected_launch)

    def test_ambiguous_program_name_asks_for_clarification(self):
        response = self.router.handle("Abra o navegador")

        self.assertIsNotNone(response)
        self.assertIn("nome completo", response)
        self.assertIn("Google Chrome", response)
        self.assertIn("Microsoft Edge", response)
        self.assertEqual(self.launched, [])

    def test_installed_program_aliases_are_resolved_without_executing_text(self):
        cases = {
            "corel drow": "coreldraw",
            "mozilla fire fox": "firefox",
            "sistema consumir": "consumer",
            "editor do arduino": "arduino",
        }

        for spoken_name, expected in cases.items():
            with self.subTest(spoken_name=spoken_name):
                resolved, ambiguous = self.router._resolve_program_name(spoken_name)
                self.assertEqual(resolved, expected)
                self.assertEqual(ambiguous, ())

    def test_any_registered_windows_application_can_be_opened(self):
        launched = []
        provider_calls = []

        def installed_programs():
            provider_calls.append(True)
            return (
                InstalledProgram("Microsoft Word", "Microsoft.Office.WINWORD.EXE.15"),
                InstalledProgram(
                    "Visual Studio Code",
                    r"{APP-GUID}\Microsoft VS Code\Code.exe",
                ),
            )

        router = ComputerCommandRouter(
            allowed_roots=self.router.allowed_roots,
            process_launcher=launched.append,
            file_opener=self.opened.append,
            key_sender=self.keys.append,
            installed_program_provider=installed_programs,
        )

        word_response = router.handle("Abra o Word")
        code_response = router.handle("Pode abrir o visual studio cod pra mim?")
        named_response = router.handle(
            "Inicie o aplicativo chamado Microsoft Word que está instalado"
        )

        self.assertIn("Microsoft Word", word_response)
        self.assertIn("Visual Studio Code", code_response)
        self.assertIn("Microsoft Word", named_response)
        self.assertEqual(
            launched,
            [
                [
                    "explorer.exe",
                    r"shell:AppsFolder\Microsoft.Office.WINWORD.EXE.15",
                ],
                [
                    "explorer.exe",
                    r"shell:AppsFolder\{APP-GUID}\Microsoft VS Code\Code.exe",
                ],
                [
                    "explorer.exe",
                    r"shell:AppsFolder\Microsoft.Office.WINWORD.EXE.15",
                ],
            ],
        )
        self.assertEqual(len(provider_calls), 1)

    def test_scripts_and_typed_commands_are_not_treated_as_installed_apps(self):
        launched = []
        router = ComputerCommandRouter(
            allowed_roots=self.router.allowed_roots,
            process_launcher=launched.append,
            file_opener=self.opened.append,
            key_sender=self.keys.append,
            installed_program_provider=lambda: (
                InstalledProgram("Ferramenta perigosa", r"C:\Temp\executar.cmd"),
            ),
        )

        script_response = router.handle("Abra a ferramenta perigosa")
        command_response = router.handle("Abra calc.exe & format c:")

        self.assertIn("não encontrei", script_response.lower())
        self.assertIn("não encontrei", command_response.lower())
        self.assertEqual(launched, [])

    def test_unregistered_program_variations_never_launch(self):
        for command in (
            "Abra o PowerShell",
            "Inicie o prompt de comando",
            "Executar o programa Word",
        ):
            with self.subTest(command=command):
                response = self.router.handle(command)
                self.assertIsNotNone(response)
                self.assertIn("não encontrei", response.lower())

        self.assertEqual(self.launched, [])

    def test_folder_variations_route_new_files_to_the_requested_root(self):
        cases = (
            ("na área de trabalho", self.desktop, "anotações.txt"),
            ("no desktop", self.desktop, "lembrete.txt"),
            ("nos documentos", self.documents, "reunião.txt"),
            ("em downloads", self.downloads, "informações.txt"),
        )

        for folder_phrase, expected_folder, filename in cases:
            with self.subTest(folder=folder_phrase):
                response = self.router.handle(
                    f"Crie um arquivo chamado {filename} {folder_phrase} "
                    "com o conteúdo português com acentuação"
                )
                path = expected_folder / filename
                self.assertIsNotNone(response)
                self.assertTrue(path.is_file())
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    "português com acentuação",
                )

    def test_accent_insensitive_reference_finds_and_reads_file(self):
        path = self.downloads / "Relatório São Paulo.md"
        path.write_text("informação pública", encoding="utf-8")

        response = self.router.handle(
            "Leia o arquivo relatorio sao paulo nos downloads"
        )

        self.assertIsNotNone(response)
        self.assertIn("Relatório São Paulo.md", response)
        self.assertIn("informação pública", response)

    def test_confirmation_synonyms_execute_pending_deletions(self):
        confirmations = ("confirmo", "pode fazer agora", "tenho certeza")

        for index, confirmation in enumerate(confirmations):
            with self.subTest(confirmation=confirmation):
                path = self.documents / f"apagar-{index}.txt"
                path.write_text("temporário", encoding="utf-8")

                request = self.router.handle(
                    f"Exclua o arquivo {path.name} nos documentos"
                )
                self.assertIsNotNone(request)
                self.assertIn("confirmar", request.lower())
                self.assertTrue(path.exists())

                response = self.router.handle(confirmation)
                self.assertIsNotNone(response)
                self.assertIn("apagado", response.lower())
                self.assertFalse(path.exists())

    def test_accented_cancellation_and_unrelated_reply_keep_files_safe(self):
        path = self.desktop / "não-apagar.txt"
        path.write_text("preservar", encoding="utf-8")
        self.router.handle(
            "Deletar o arquivo não-apagar.txt na área de trabalho"
        )

        waiting = self.router.handle("talvez depois")
        self.assertIsNotNone(waiting)
        self.assertIn("Ainda aguardo confirmação", waiting)
        self.assertTrue(path.exists())

        cancelled = self.router.handle("não faça isso")
        self.assertIsNotNone(cancelled)
        self.assertIn("cancelada", cancelled.lower())
        self.assertTrue(path.exists())
        self.assertIsNone(self.router.pending_action)

    def test_existing_file_is_not_overwritten_without_confirmation(self):
        path = self.documents / "agenda.txt"
        path.write_text("conteúdo original", encoding="utf-8")

        warning = self.router.handle(
            "Cria um arquivo agenda.txt nos documentos contendo conteúdo novo"
        )
        self.assertIsNotNone(warning)
        self.assertIn("já existe", warning)
        self.assertEqual(path.read_text(encoding="utf-8"), "conteúdo original")

        waiting = self.router.handle("continue conversando")
        self.assertIsNotNone(waiting)
        self.assertIn("confirmar ou cancelar", waiting)
        self.assertEqual(path.read_text(encoding="utf-8"), "conteúdo original")

        self.router.handle("pode executar")
        self.assertEqual(path.read_text(encoding="utf-8"), "conteúdo novo")

    def test_ambiguous_filename_requires_folder_before_reading(self):
        desktop_file = self.desktop / "relatório mensal.txt"
        downloads_file = self.downloads / "relatório mensal.txt"
        desktop_file.write_text("versão da área de trabalho", encoding="utf-8")
        downloads_file.write_text("versão de downloads", encoding="utf-8")

        ambiguous = self.router.handle("Leia o arquivo relatório mensal.txt")
        self.assertIsNotNone(ambiguous)
        self.assertIn("mais de um arquivo", ambiguous)
        self.assertIn("Especifique a pasta", ambiguous)
        self.assertNotIn("versão da área de trabalho", ambiguous)
        self.assertNotIn("versão de downloads", ambiguous)

        resolved = self.router.handle(
            "Leia o arquivo relatorio mensal.txt nos downloads"
        )
        self.assertIsNotNone(resolved)
        self.assertIn("versão de downloads", resolved)
        self.assertNotIn("versão da área de trabalho", resolved)

    def test_ambiguous_filename_is_not_opened(self):
        (self.documents / "foto férias.png").write_bytes(b"documents")
        (self.downloads / "foto férias.png").write_bytes(b"downloads")

        response = self.router.handle("Abra o arquivo foto ferias.png")

        self.assertIsNotNone(response)
        self.assertIn("mais de um arquivo", response)
        self.assertEqual(self.opened, [])


if __name__ == "__main__":
    unittest.main()

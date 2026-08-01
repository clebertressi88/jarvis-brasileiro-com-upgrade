import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from jarvis_programmer import ProgrammerAgent


class ProgrammerCompileAndExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.profile = self.base / "Profile"
        self.workspace = self.base / "Workspace"
        self.desktop = self.profile / "Desktop"
        self.documents = self.profile / "Documents"
        self.downloads = self.profile / "Downloads"
        for folder in (
            self.workspace,
            self.desktop,
            self.documents,
            self.downloads,
        ):
            folder.mkdir(parents=True)

        self.environment = patch.dict(
            os.environ,
            {"USERPROFILE": str(self.profile)},
        )
        self.environment.start()
        self.agent = ProgrammerAgent(
            workspace=self.workspace,
            code_generator=lambda _spec, _request, _current: 'print("ok")\n',
            process_launcher=lambda _command, _cwd: None,
        )

    def tearDown(self):
        self.environment.stop()
        self.temp_dir.cleanup()

    def _create_python_project(self, name="demo"):
        project = self.workspace / name
        metadata = project / ".jarvis"
        metadata.mkdir(parents=True)
        source = project / "main.py"
        source.write_text('print("original")\n', encoding="utf-8")
        manifest = {
            "id": name,
            "name": name,
            "language": "python",
            "entrypoint": "main.py",
        }
        (metadata / "project.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        return project, source

    def _write_zip(self, filename, members):
        archive_path = self.downloads / filename
        with zipfile.ZipFile(archive_path, "w") as archive:
            for member_name, content in members:
                archive.writestr(member_name, content)
        return archive_path

    def _destination_for(self, archive_path):
        base_name = archive_path.name
        for suffix in (".tar.gz", ".tgz", ".tar", ".zip"):
            if base_name.lower().endswith(suffix):
                base_name = base_name[: -len(suffix)]
                break
        return self.workspace / f"importado-{base_name.lower()}"

    def test_compilation_waits_for_exact_confirmation_and_uses_shell_false(self):
        project, source = self._create_python_project()

        with patch("jarvis_programmer.agent.subprocess.run") as run:
            request = self.agent.handle("Compile o projeto demo")
            self.assertIsNotNone(request)
            self.assertIn("confirmar compilação", request)
            run.assert_not_called()

            generic = self.agent.handle("confirmar")
            self.assertIsNotNone(generic)
            self.assertIn("diga exatamente", generic.lower())
            run.assert_not_called()

            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            response = self.agent.handle("confirmar compilação")

        self.assertIsNotNone(response)
        self.assertIn("Compilação concluída", response)
        run.assert_called_once()
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:3], ["-m", "py_compile"])
        self.assertEqual(Path(argv[3]), source)
        self.assertEqual(run.call_args.kwargs["cwd"], project)
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_changed_source_cancels_compilation_before_process_start(self):
        _project, source = self._create_python_project()

        with patch("jarvis_programmer.agent.subprocess.run") as run:
            self.agent.handle("Compilar o projeto demo")
            source.write_text('print("alterado fora do Jarvis")\n', encoding="utf-8")
            response = self.agent.handle("confirmar compilação")

        self.assertIsNotNone(response)
        self.assertIn("mudou após o pedido", response)
        run.assert_not_called()

    def test_safe_zip_extracts_only_after_exact_confirmation(self):
        archive_path = self._write_zip(
            "projeto.zip",
            [("src/main.txt", b"arquivo seguro")],
        )
        destination = self._destination_for(archive_path)

        request = self.agent.handle("Descompacte projeto.zip nos downloads")
        self.assertIsNotNone(request)
        self.assertIn("confirmar descompactação", request)
        self.assertFalse(destination.exists())

        generic = self.agent.handle("confirmar")
        self.assertIsNotNone(generic)
        self.assertIn("diga exatamente", generic.lower())
        self.assertFalse(destination.exists())

        response = self.agent.handle("confirmar descompactação")
        self.assertIsNotNone(response)
        self.assertIn("Arquivo descompactado", response)
        self.assertEqual(
            (destination / "src" / "main.txt").read_bytes(),
            b"arquivo seguro",
        )

    def test_zip_traversal_and_absolute_paths_are_rejected(self):
        cases = (
            ("travessia.zip", "../escape.txt"),
            ("absoluto.zip", "/absolute.txt"),
            ("unidade.zip", "C:/Windows/escape.txt"),
        )

        for filename, member_name in cases:
            with self.subTest(member_name=member_name):
                archive_path = self._write_zip(filename, [(member_name, b"malicioso")])
                destination = self._destination_for(archive_path)
                outside_escape = self.workspace.parent / "escape.txt"

                self.agent.handle(f"Extraia {filename} nos downloads")
                response = self.agent.handle("confirmar descompactação")

                self.assertIsNotNone(response)
                self.assertTrue(
                    any(
                        term in response.lower()
                        for term in ("perigoso", "inválido", "sair da pasta")
                    ),
                    response,
                )
                self.assertFalse(destination.exists())
                self.assertFalse(outside_escape.exists())

    def test_zip_symbolic_link_entry_is_rejected(self):
        archive_path = self.downloads / "link.zip"
        link = zipfile.ZipInfo("atalho")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(link, "destino.txt")

        self.agent.handle("Descompacte link.zip nos downloads")
        response = self.agent.handle("confirmar descompactação")

        self.assertIsNotNone(response)
        self.assertIn("links", response.lower())
        self.assertFalse(self._destination_for(archive_path).exists())

    def test_tar_symbolic_and_hard_link_entries_are_rejected(self):
        cases = (
            ("simbolico.tar", tarfile.SYMTYPE),
            ("hardlink.tar", tarfile.LNKTYPE),
        )

        for filename, link_type in cases:
            with self.subTest(link_type=link_type):
                archive_path = self.downloads / filename
                with tarfile.open(archive_path, "w") as archive:
                    regular = tarfile.TarInfo("destino.txt")
                    data = b"destino"
                    regular.size = len(data)
                    archive.addfile(regular, io.BytesIO(data))
                    link = tarfile.TarInfo("atalho.txt")
                    link.type = link_type
                    link.linkname = "destino.txt"
                    archive.addfile(link)

                self.agent.handle(f"Extraia {filename} nos downloads")
                response = self.agent.handle("confirmar descompactação")

                self.assertIsNotNone(response)
                self.assertIn("links", response.lower())
                self.assertFalse(self._destination_for(archive_path).exists())

    def test_changed_archive_cancels_extraction(self):
        archive_path = self._write_zip("mutavel.zip", [("antes.txt", b"antes")])
        destination = self._destination_for(archive_path)
        self.agent.handle("Descompacte mutavel.zip nos downloads")

        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("depois.txt", b"depois")
        response = self.agent.handle("confirmar descompactação")

        self.assertIsNotNone(response)
        self.assertIn("mudou após o pedido", response)
        self.assertFalse(destination.exists())

    def test_existing_destination_is_not_overwritten(self):
        archive_path = self._write_zip("ocupado.zip", [("novo.txt", b"novo")])
        destination = self._destination_for(archive_path)
        self.agent.handle("Descompacte ocupado.zip nos downloads")

        destination.mkdir()
        existing = destination / "existente.txt"
        existing.write_text("preservar", encoding="utf-8")
        response = self.agent.handle("confirmar descompactação")

        self.assertIsNotNone(response)
        self.assertIn("destino", response.lower())
        self.assertEqual(existing.read_text(encoding="utf-8"), "preservar")
        self.assertFalse((destination / "novo.txt").exists())

    def test_rar_and_7z_are_refused_without_pending_action(self):
        for filename in ("arquivo.rar", "arquivo.7z"):
            with self.subTest(filename=filename):
                (self.downloads / filename).write_bytes(b"formato nao autorizado")

                response = self.agent.handle(
                    f"Descompacte {filename} nos downloads"
                )

                self.assertIsNotNone(response)
                self.assertIn("apenas arquivos ZIP, TAR, TAR.GZ e TGZ", response)
                self.assertIsNone(self.agent.pending_action)


if __name__ == "__main__":
    unittest.main()

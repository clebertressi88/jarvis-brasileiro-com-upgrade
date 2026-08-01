import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis_installer.agent import InstallerAgent, TRUSTED_PACKAGES


class FakeWingetRunner:
    def __init__(self):
        self.calls = []
        self.show_outputs = []
        self.show_returncode = 0
        self.install_returncode = 0
        self.install_stdout = "instalação concluída"
        self.install_stderr = ""
        self.install_exception = None
        self.list_returncode = 0
        self.list_stdout = "Git.Git 2.50.0"
        self.list_stderr = ""

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, dict(kwargs)))
        action = argv[1]

        if action == "show":
            if self.show_outputs:
                stdout = self.show_outputs.pop(0)
            else:
                package_id = argv[argv.index("--id") + 1]
                stdout = f"Id: {package_id}\nVersion: 1.2.3\n"
            return subprocess.CompletedProcess(
                argv,
                self.show_returncode,
                stdout=stdout,
                stderr="",
            )

        if action == "install":
            if self.install_exception is not None:
                raise self.install_exception
            return subprocess.CompletedProcess(
                argv,
                self.install_returncode,
                stdout=self.install_stdout,
                stderr=self.install_stderr,
            )

        if action == "list":
            return subprocess.CompletedProcess(
                argv,
                self.list_returncode,
                stdout=self.list_stdout,
                stderr=self.list_stderr,
            )

        raise AssertionError(f"ação winget inesperada: {action}")

    def calls_for(self, action):
        return [call for call in self.calls if call[0][1] == action]


class InstallerAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.winget_path = (
            self.base
            / "Program Files"
            / "WindowsApps"
            / "Microsoft.DesktopAppInstaller_1.0.0.0_x64__8wekyb3d8bbwe"
            / "winget.exe"
        )
        self.winget_path.parent.mkdir(parents=True)
        self.winget_path.write_bytes(b"W" * 10_001)
        self.audit_log = self.base / "audit" / "installer.jsonl"
        self.runner = FakeWingetRunner()
        self.agent = self._new_agent()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _new_agent(self, *, runner=None, ttl=60.0):
        return InstallerAgent(
            winget_path=self.winget_path,
            runner=runner or self.runner,
            confirmation_ttl_seconds=ttl,
            audit_log_path=self.audit_log,
        )

    @staticmethod
    def _metadata(package_id, version):
        return f"Id: {package_id}\nVersion: {version}\n"

    def test_allowlist_aliases_map_only_to_the_expected_packages(self):
        expected_ids = {
            "Microsoft.VisualStudioCode",
            "7zip.7zip",
            "Git.Git",
            "Python.Python.3.12",
            "OpenJS.NodeJS.LTS",
            "Notepad++.Notepad++",
            "VideoLAN.VLC",
            "Mozilla.Firefox",
            "Google.Chrome",
            "Microsoft.PowerToys",
            "ArduinoSA.IDE.stable",
        }
        self.assertEqual({package.package_id for package in TRUSTED_PACKAGES}, expected_ids)

        for package in TRUSTED_PACKAGES:
            for alias in package.aliases:
                with self.subTest(package=package.key, alias=alias):
                    resolved = InstallerAgent._package_from_alias(alias)
                    self.assertIsNotNone(resolved)
                    self.assertEqual(resolved.package_id, package.package_id)

    def test_unauthorized_program_is_rejected_without_calling_winget(self):
        for command in ("Instale o Steam", "Instalar TeamViewer", "Instale um antivírus"):
            with self.subTest(command=command):
                response = self.agent.handle(command)
                self.assertIsNotNone(response)
                self.assertIn("não está na lista autorizada", response)

        self.assertEqual(self.runner.calls, [])
        self.assertIsNone(self.agent.pending_installation)

    def test_package_metadata_must_succeed_and_contain_the_exact_id(self):
        cases = (
            (1, "winget failed", "não encontrou o ID exato"),
            (0, "Id: Pacote.Errado\nVersion: 1.0", "não corresponde"),
        )

        for returncode, output, expected in cases:
            with self.subTest(returncode=returncode, output=output):
                runner = FakeWingetRunner()
                runner.show_returncode = returncode
                runner.show_outputs = [output]
                agent = self._new_agent(runner=runner)

                response = agent.handle("Instale o Git")

                self.assertIsNotNone(response)
                self.assertIn("Não consegui verificar", response)
                self.assertIn(expected, response)
                self.assertIsNone(agent.pending_installation)
                self.assertEqual(runner.calls_for("install"), [])

    def test_installation_waits_for_exact_confirmation(self):
        request = self.agent.handle("Instale o Git")
        self.assertIsNotNone(request)
        self.assertIn("confirmar instalação", request)
        self.assertEqual(self.runner.calls_for("install"), [])

        generic = self.agent.handle("confirmar")
        self.assertIsNotNone(generic)
        self.assertIn("diga exatamente", generic.lower())
        self.assertEqual(self.runner.calls_for("install"), [])

        self.runner.list_stdout = "Git.Git 1.2.3"
        response = self.agent.handle("confirmar instalação")
        self.assertIsNotNone(response)
        self.assertIn("instalado com sucesso", response)
        self.assertEqual(len(self.runner.calls_for("install")), 1)

    def test_cancellation_and_expiration_never_install(self):
        self.agent.handle("Instale o Git")
        cancelled = self.agent.handle("não, cancele")
        self.assertIsNotNone(cancelled)
        self.assertIn("cancelada", cancelled.lower())
        self.assertIsNone(self.agent.pending_installation)
        self.assertEqual(self.runner.calls_for("install"), [])

        expired_runner = FakeWingetRunner()
        expired_agent = self._new_agent(runner=expired_runner, ttl=-1)
        expired_agent.handle("Instale o Git")
        expired = expired_agent.handle("confirmar instalação")
        self.assertIsNotNone(expired)
        self.assertIn("expirou", expired.lower())
        self.assertIsNone(expired_agent.pending_installation)
        self.assertEqual(expired_runner.calls_for("install"), [])

    def test_install_argv_is_fixed_safe_and_uses_shell_false(self):
        self.agent.handle("Instale o Git")
        self.runner.list_stdout = "Git.Git 1.2.3"
        self.agent.handle("confirmar instalação")

        install_calls = self.runner.calls_for("install")
        self.assertEqual(len(install_calls), 1)
        argv, kwargs = install_calls[0]
        self.assertEqual(
            argv,
            [
                str(self.winget_path.resolve()),
                "install",
                "--id",
                "Git.Git",
                "--exact",
                "--source",
                "winget",
                "--silent",
                "--disable-interactivity",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--no-upgrade",
            ],
        )
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 900)
        forbidden = {
            "--force",
            "--override",
            "--allow-reboot",
            "--ignore-security-hash",
        }
        self.assertTrue(forbidden.isdisjoint(argv))

    def test_changed_metadata_cancels_before_installation(self):
        self.runner.show_outputs = [
            self._metadata("Git.Git", "1.0"),
            self._metadata("Git.Git", "2.0"),
        ]
        self.agent.handle("Instale o Git")

        response = self.agent.handle("confirmar instalação")

        self.assertIsNotNone(response)
        self.assertIn("metadados ou a versão", response)
        self.assertIn("cancelada por segurança", response)
        self.assertEqual(self.runner.calls_for("install"), [])
        self.assertIsNone(self.agent.pending_installation)

    def test_changed_winget_cancels_before_metadata_recheck_or_install(self):
        self.agent.handle("Instale o Git")
        self.winget_path.write_bytes(b"X" * 10_002)

        response = self.agent.handle("confirmar instalação")

        self.assertIsNotNone(response)
        self.assertIn("winget mudou após o pedido", response)
        self.assertIn("cancelada por segurança", response)
        self.assertEqual(len(self.runner.calls_for("show")), 1)
        self.assertEqual(self.runner.calls_for("install"), [])

    def test_success_requires_post_installation_verification(self):
        self.agent.handle("Instale o Git")
        self.runner.list_stdout = "nenhum pacote correspondente"
        unverified = self.agent.handle("confirmar instalação")
        self.assertIsNotNone(unverified)
        self.assertIn("não consegui confirmar", unverified)
        self.assertNotIn("instalado com sucesso", unverified)
        self.assertEqual(len(self.runner.calls_for("list")), 1)

        verified_runner = FakeWingetRunner()
        verified_runner.list_stdout = "Git.Git 1.2.3"
        verified_agent = self._new_agent(runner=verified_runner)
        verified_agent.handle("Instale o Git")
        verified = verified_agent.handle("confirmar instalação")
        self.assertIsNotNone(verified)
        self.assertIn("instalado com sucesso", verified)
        self.assertEqual(len(verified_runner.calls_for("list")), 1)

    def test_install_failure_reports_exit_code_and_does_not_verify(self):
        self.runner.install_returncode = 42
        self.runner.install_stdout = ""
        self.runner.install_stderr = "falha simulada"
        self.agent.handle("Instale o Git")

        response = self.agent.handle("confirmar instalação")

        self.assertIsNotNone(response)
        self.assertIn("falhou com código 42", response)
        self.assertIn("falha simulada", response)
        self.assertEqual(self.runner.calls_for("list"), [])

    def test_install_timeout_reports_uncertain_result_and_does_not_verify(self):
        self.runner.install_exception = subprocess.TimeoutExpired(
            cmd=[str(self.winget_path), "install"],
            timeout=900,
        )
        self.agent.handle("Instale o Git")

        response = self.agent.handle("confirmar instalação")

        self.assertIsNotNone(response)
        self.assertIn("ultrapassou 15 minutos", response)
        self.assertIn("resultado ficou incerto", response)
        self.assertEqual(self.runner.calls_for("list"), [])


if __name__ == "__main__":
    unittest.main()

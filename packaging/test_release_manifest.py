from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import json

from release_manifest import validate_launcher
from launcher import choose_port, health_is_ready


ROOT = Path(__file__).resolve().parents[1]


class LauncherIntegrityTests(unittest.TestCase):
    def staged(self, cmd=None, launcher=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        stage = Path(temporary.name)
        (stage / 'app').mkdir()
        (stage / 'Start RAS-Plot.cmd').write_text(
            cmd if cmd is not None else (ROOT / 'packaging/Start RAS-Plot.cmd').read_text(encoding='utf-8-sig'))
        (stage / 'app/launcher.py').write_text(
            launcher if launcher is not None else (ROOT / 'packaging/launcher.py').read_text(encoding='utf-8-sig'))
        return stage

    def test_current_launcher_is_package_relative_and_fallbacks_are_rejected(self):
        result = validate_launcher(self.staged())
        self.assertEqual(result['interpreter'], r'runtime\python\python.exe')
        for command in ('python app\\launcher.py', 'python.exe app\\launcher.py',
                        'py app\\launcher.py', 'where python'):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'fallback|exactly once'):
                validate_launcher(self.staged(cmd=command))

    def test_port_selection_prefers_default_then_skips_occupied_port(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            occupied = listener.getsockname()[1]
            self.assertEqual(choose_port(occupied, 2), occupied + 1)

    def test_health_identity_matches_current_application_only(self):
        self.assertTrue(health_is_ready(200, {
            'ok': True, 'application': 'RAS-Plot Replica',
        }))
        self.assertFalse(health_is_ready(200, {'ok': True, 'application': 'RAS-Plot'}))
        self.assertFalse(health_is_ready(503, {'ok': True, 'application': 'RAS-Plot Replica'}))

    def test_packaging_uses_tracked_neutral_configuration(self):
        settings = json.loads((ROOT / 'settings.package.json').read_text(encoding='utf-8'))
        self.assertEqual(settings, {
            'host': '127.0.0.1',
            'port': 8765,
            'last_plan_path': '',
            'default_fis': {},
        })
        for name in ('build_rc4.ps1', 'build_team_review.ps1', 'rebuild_handoff.ps1'):
            script = (ROOT / 'packaging' / name).read_text(encoding='utf-8-sig')
            self.assertIn('settings.package.json', script, name)
            self.assertNotIn('$sourceRoot\\settings.local.json', script, name)



if __name__ == '__main__':
    unittest.main()

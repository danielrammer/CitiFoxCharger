import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from citifoxcharger.browser import find_brave, open_dashboard


class BrowserTests(unittest.TestCase):
    def test_finds_windows_installation_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="browser test ") as directory:
            executable = Path(directory) / "BraveSoftware/Brave-Browser/Application/brave.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict("os.environ", {"PROGRAMFILES": directory}, clear=True):
                self.assertEqual(Path(find_brave()), executable)

    @patch("citifoxcharger.browser.webbrowser.open")
    @patch("citifoxcharger.browser.subprocess.Popen")
    @patch("citifoxcharger.browser.find_brave", return_value=r"C:\Program Files\Brave\brave.exe")
    def test_fullscreen_uses_argument_list_without_shell(self, find, process, default):
        url = "http://127.0.0.1:8765"
        self.assertEqual(open_dashboard(url, fullscreen=True), "brave")
        self.assertEqual(process.call_args.args[0], [find.return_value, "--new-window", "--start-fullscreen", url])
        self.assertNotIn("shell", process.call_args.kwargs)
        default.assert_not_called()

    @patch("citifoxcharger.browser.webbrowser.open")
    @patch("citifoxcharger.browser.subprocess.Popen")
    @patch("citifoxcharger.browser.find_brave", return_value="brave")
    def test_windowed_start_does_not_force_fullscreen(self, find, process, default):
        open_dashboard("http://127.0.0.1:8765")
        self.assertNotIn("--start-fullscreen", process.call_args.args[0])

    @patch("citifoxcharger.browser.webbrowser.open")
    @patch("citifoxcharger.browser.find_brave", return_value=None)
    def test_missing_brave_falls_back_to_default_browser(self, find, default):
        self.assertEqual(open_dashboard("http://127.0.0.1:8765", fullscreen=True), "default")
        default.assert_called_once_with("http://127.0.0.1:8765", new=1)

    @patch("citifoxcharger.browser.webbrowser.open")
    @patch("citifoxcharger.browser.subprocess.Popen", side_effect=OSError)
    @patch("citifoxcharger.browser.find_brave", return_value="brave")
    def test_launch_failure_falls_back(self, find, process, default):
        self.assertEqual(open_dashboard("http://127.0.0.1:8765"), "default")
        default.assert_called_once()

    @patch("citifoxcharger.browser.webbrowser.open")
    @patch("citifoxcharger.browser.find_brave")
    def test_explicit_default_browser_skips_brave(self, find, default):
        self.assertEqual(open_dashboard("http://127.0.0.1:8765", browser="default"), "default")
        find.assert_not_called()

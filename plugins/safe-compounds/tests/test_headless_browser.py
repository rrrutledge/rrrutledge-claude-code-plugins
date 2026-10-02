"""Unit tests for the headless-browser render checker and for first_word's
handling of a quoted executable path that contains spaces."""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, PLUGIN_DIR)

from safe_compounds.shell import first_word  # noqa: E402
from safe_compounds.commands import is_headless_browser_safe  # noqa: E402

EDGE = '"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"'
OUT = 'C:\\\\Users\\\\russe\\\\.claude\\\\jobs\\\\abc\\\\tmp\\\\light.png'
PAGE = 'file:///C:/Users/russe/dev/repo/.tmp/page.html'


class TestFirstWordQuotedPath:
    def test_quoted_path_with_spaces(self):
        assert first_word(EDGE + ' --headless x') == 'msedge'

    def test_single_quoted_path_with_spaces(self):
        assert first_word("'/c/Program Files/x/tool.exe' arg") == 'tool'

    def test_unquoted_path_unchanged(self):
        assert first_word('/usr/bin/git status') == 'git'


class TestHeadlessBrowser:
    def test_screenshot_to_jobs_dir_of_local_file(self):
        seg = f'{EDGE} --headless=new --disable-gpu --screenshot="{OUT}" --window-size=1200,7000 "{PAGE}"'
        assert is_headless_browser_safe(seg) is True

    def test_dark_mode_flags(self):
        seg = (f'{EDGE} --headless=new --force-dark-mode '
               f'--blink-settings=preferredColorScheme=0 --screenshot="{OUT}" "{PAGE}"')
        assert is_headless_browser_safe(seg) is True

    def test_localhost_page(self):
        assert is_headless_browser_safe(f'{EDGE} --headless --screenshot="{OUT}" http://localhost:3000/') is True

    def test_not_headless_prompts(self):
        assert is_headless_browser_safe(f'{EDGE} --screenshot="{OUT}" "{PAGE}"') is False

    def test_screenshot_outside_trusted_dir_prompts(self):
        seg = f'{EDGE} --headless --screenshot="C:\\\\Windows\\\\System32\\\\x.png" "{PAGE}"'
        assert is_headless_browser_safe(seg) is False

    def test_remote_url_prompts(self):
        assert is_headless_browser_safe(f'{EDGE} --headless --screenshot="{OUT}" https://example.com') is False

    def test_debugging_port_prompts(self):
        seg = f'{EDGE} --headless --remote-debugging-port=9222 --screenshot="{OUT}" "{PAGE}"'
        assert is_headless_browser_safe(seg) is False

    def test_user_data_dir_prompts(self):
        seg = f'{EDGE} --headless --user-data-dir=C:\\\\x --screenshot="{OUT}" "{PAGE}"'
        assert is_headless_browser_safe(seg) is False

    def test_no_page_prompts(self):
        assert is_headless_browser_safe(f'{EDGE} --headless --screenshot="{OUT}"') is False

    def test_other_program_not_matched(self):
        assert is_headless_browser_safe('notepad --headless x') is False

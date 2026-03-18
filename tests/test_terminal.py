"""
Tests for terminal tools.
"""

import tempfile
import pytest

from tools.terminal import TerminalTools


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def terminal(workspace):
    return TerminalTools(workspace)


class TestRunCommand:
    def test_simple_command(self, terminal):
        result = terminal.run_command("echo hello")
        assert "EXIT CODE: 0" in result
        assert "hello" in result

    def test_command_with_error(self, terminal):
        result = terminal.run_command("ls /nonexistent_directory_xyz 2>&1")
        assert "EXIT CODE:" in result

    def test_blocked_dangerous_command(self, terminal):
        result = terminal.run_command("rm -rf /")
        assert "BLOCKED" in result

    def test_another_blocked_command(self, terminal):
        result = terminal.run_command("DROP TABLE users")
        assert "BLOCKED" in result

    def test_command_timeout(self, terminal):
        # This should not hang forever
        result = terminal.run_command("echo quick")
        assert "EXIT CODE: 0" in result

    def test_command_in_workspace(self, terminal, workspace):
        result = terminal.run_command("pwd")
        assert workspace in result


"""
Tests for filesystem tools.
"""

import tempfile
import os
import pytest

from tools.filesystem import FilesystemTools


@pytest.fixture
def workspace():
    """Create a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files
        (f := open(os.path.join(tmpdir, "hello.py"), "w")).write("print('hello')\n")
        f.close()

        subdir = os.path.join(tmpdir, "src")
        os.makedirs(subdir)
        (f := open(os.path.join(subdir, "main.py"), "w")).write(
            "def main():\n    return 42\n"
        )
        f.close()

        yield tmpdir


@pytest.fixture
def fs(workspace):
    return FilesystemTools(workspace)


class TestReadFile:
    def test_read_existing_file(self, fs):
        result = fs.read_file("hello.py")
        assert "print('hello')" in result
        assert "FILE: hello.py" in result

    def test_read_nonexistent_file(self, fs):
        result = fs.read_file("nope.py")
        assert "ERROR" in result
        assert "not found" in result.lower()

    def test_read_file_in_subdir(self, fs):
        result = fs.read_file("src/main.py")
        assert "def main()" in result

    def test_read_file_with_line_numbers(self, fs):
        result = fs.read_file("src/main.py")
        assert "1 |" in result or "   1 |" in result


class TestEditFile:
    def test_edit_file_success(self, fs, workspace):
        result = fs.edit_file("hello.py", "print('hello')", "print('world')")
        assert "OK" in result

        # Verify the change
        content = open(os.path.join(workspace, "hello.py")).read()
        assert "print('world')" in content

    def test_edit_file_not_found(self, fs):
        result = fs.edit_file("nope.py", "old", "new")
        assert "ERROR" in result

    def test_edit_file_string_not_found(self, fs):
        result = fs.edit_file("hello.py", "this_does_not_exist", "new")
        assert "ERROR" in result
        assert "not found" in result.lower()


class TestCreateFile:
    def test_create_new_file(self, fs, workspace):
        result = fs.create_file("new_file.txt", "hello world")
        assert "OK" in result

        content = open(os.path.join(workspace, "new_file.txt")).read()
        assert content == "hello world"

    def test_create_file_with_subdirs(self, fs, workspace):
        result = fs.create_file("deep/nested/file.py", "# deep")
        assert "OK" in result
        assert os.path.exists(os.path.join(workspace, "deep", "nested", "file.py"))


class TestListDirectory:
    def test_list_root(self, fs):
        result = fs.list_directory(".")
        assert "hello.py" in result
        assert "src" in result

    def test_list_subdir(self, fs):
        result = fs.list_directory("src")
        assert "main.py" in result

    def test_list_nonexistent(self, fs):
        result = fs.list_directory("nope")
        assert "ERROR" in result


class TestPathSafety:
    def test_path_escape_blocked(self, fs):
        with pytest.raises(PermissionError):
            fs.read_file("../../etc/passwd")


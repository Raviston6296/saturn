"""
Tests for DpaasInitializer — one-time DPAAS_HOME startup extraction.

The initializer must:
  1. Extract DPAAS_SOURCE_TAR → DPAAS_HOME/zdpas/spark/jars/ (at minimum)
  2. Extract DPAAS_TEST_TAR resources → DPAAS_HOME/zdpas/spark/resources/
  3. Copy BUILD_FILE_HOME/datastore.json → DPAAS_HOME/zdpas/spark/resources/
  4. Write a sentinel file so subsequent calls are skipped (idempotent)
  5. Emit clear errors when DPAAS_HOME or DPAAS_SOURCE_TAR are absent
"""

from __future__ import annotations

import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from dpaas import DpaasInitializer, ensure_dpaas_ready


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_source_tar(dest: Path, jar_names: list[str] | None = None) -> Path:
    """Create a minimal dpaas.tar.gz with the expected internal layout."""
    jar_names = jar_names or ["spark-core.jar", "scala-library.jar"]
    tar_path = dest / "dpaas.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        jars_dir = tmp_path / "zdpas" / "spark" / "jars"
        lib_dir = tmp_path / "zdpas" / "spark" / "lib"
        jars_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True)
        for name in jar_names:
            (jars_dir / name).write_bytes(b"fake-jar")
        (lib_dir / "extra.jar").write_bytes(b"fake-lib")

        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(tmp_path / "zdpas", arcname="zdpas")

    return tar_path


def _make_test_tar(dest: Path, resource_names: list[str] | None = None) -> Path:
    """Create a minimal dpaas_test.tar.gz with test resources."""
    resource_names = resource_names or ["schema.json", "test-data.csv"]
    tar_path = dest / "dpaas_test.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        res_dir = tmp_path / "zdpas" / "spark" / "resources"
        res_dir.mkdir(parents=True)
        for name in resource_names:
            (res_dir / name).write_bytes(b"test-resource")

        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(tmp_path / "zdpas", arcname="zdpas")

    return tar_path


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDpaasInitializerBasic:
    """Core extraction and sentinel behaviour."""

    def test_returns_false_when_dpaas_home_not_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DPAAS_HOME", raising=False)
        init = DpaasInitializer(
            dpaas_home="",
            source_tar=str(tmp_path / "nope.tar.gz"),
        )
        # Patch settings to also be empty
        with patch("dpaas.settings") as mock_settings:
            mock_settings.saturn_dpaas_home = ""
            init.dpaas_home = Path("")
            result = init.ensure_ready()
        assert result is False

    def test_returns_false_when_source_tar_missing(self, tmp_path):
        dpaas_home = tmp_path / "dpaas"
        init = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(tmp_path / "nonexistent.tar.gz"),
        )
        result = init.ensure_ready()
        assert result is False

    def test_extracts_jars_from_source_tar(self, tmp_path):
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()

        source_tar = _make_source_tar(tars_dir, ["spark-core.jar", "hadoop.jar"])

        init = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        )
        result = init.ensure_ready()

        assert result is True
        jars_dir = dpaas_home / "zdpas" / "spark" / "jars"
        assert jars_dir.is_dir()
        jar_names = {p.name for p in jars_dir.glob("*.jar")}
        assert "spark-core.jar" in jar_names
        assert "hadoop.jar" in jar_names

    def test_writes_sentinel_file(self, tmp_path):
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        init = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        )
        init.ensure_ready()

        sentinel = dpaas_home / ".saturn_dpaas_ready"
        assert sentinel.exists(), "Sentinel file must be written after successful init"

    def test_skips_extraction_when_sentinel_exists(self, tmp_path):
        """Second call must be a no-op (idempotent)."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        init = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        )
        init.ensure_ready()

        # Remove the tar so a second extraction would fail
        source_tar.unlink()

        init2 = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        )
        result = init2.ensure_ready()
        assert result is True, "Should return True (already initialised)"

    def test_force_reinit_re_extracts(self, tmp_path):
        """force=True bypasses the sentinel and re-extracts."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        # First init
        DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        ).ensure_ready()

        # Verify sentinel is present
        assert (dpaas_home / ".saturn_dpaas_ready").exists()

        # Second init with force=True should run again (no error expected)
        result = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            force=True,
        ).ensure_ready()
        assert result is True

    def test_creates_app_blue_dir(self, tmp_path):
        """app_blue directory must exist after extraction."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        ).ensure_ready()

        assert (dpaas_home / "zdpas" / "spark" / "app_blue").is_dir()

    def test_writes_default_log4j_when_absent(self, tmp_path):
        """A default log4j-local.properties must be written."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        ).ensure_ready()

        log4j = dpaas_home / "zdpas" / "spark" / "conf" / "log4j-local.properties"
        assert log4j.exists()
        assert "log4j.rootLogger" in log4j.read_text()


class TestDpaasInitializerTestTar:
    """Test-tar resource extraction."""

    def test_extracts_test_resources(self, tmp_path):
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)
        test_tar = _make_test_tar(tars_dir, ["schema.json", "config.xml"])

        DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            test_tar=str(test_tar),
        ).ensure_ready()

        resources_dir = dpaas_home / "zdpas" / "spark" / "resources"
        assert (resources_dir / "schema.json").exists()
        assert (resources_dir / "config.xml").exists()

    def test_skipped_when_test_tar_absent(self, tmp_path):
        """Missing test tar should be skipped, not cause a failure."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        result = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            test_tar=str(tmp_path / "nonexistent_test.tar.gz"),
        ).ensure_ready()

        assert result is True  # overall success even without test tar

    def test_skipped_when_test_tar_not_set(self, tmp_path):
        """No test tar configured → resources dir still created, no error."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        result = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            test_tar="",
        ).ensure_ready()

        assert result is True


class TestDpaasInitializerDatastoreJson:
    """datastore.json copy from BUILD_FILE_HOME."""

    def test_copies_datastore_json(self, tmp_path):
        dpaas_home = tmp_path / "dpaas"
        build_home = tmp_path / "build-files"
        build_home.mkdir()
        (build_home / "datastore.json").write_text('{"tables": []}')

        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            build_file_home=str(build_home),
        ).ensure_ready()

        dest = dpaas_home / "zdpas" / "spark" / "resources" / "datastore.json"
        assert dest.exists()
        assert dest.read_text() == '{"tables": []}'

    def test_skipped_when_build_file_home_absent(self, tmp_path):
        """Missing BUILD_FILE_HOME → skipped gracefully."""
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        result = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            build_file_home=str(tmp_path / "no-such-dir"),
        ).ensure_ready()

        assert result is True  # should not fail

    def test_skipped_when_datastore_json_missing(self, tmp_path):
        """BUILD_FILE_HOME exists but no datastore.json → skipped, not fatal."""
        dpaas_home = tmp_path / "dpaas"
        build_home = tmp_path / "build-files"
        build_home.mkdir()  # empty dir, no datastore.json

        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        result = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
            build_file_home=str(build_home),
        ).ensure_ready()

        assert result is True


class TestDpaasInitializerEnvVar:
    """DpaasInitializer resolves settings from env vars."""

    def test_uses_dpaas_home_env_var(self, tmp_path, monkeypatch):
        dpaas_home = tmp_path / "from-env"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        monkeypatch.setenv("DPAAS_HOME", str(dpaas_home))
        monkeypatch.setenv("DPAAS_SOURCE_TAR", str(source_tar))
        monkeypatch.delenv("BUILD_FILE_HOME", raising=False)

        with patch("dpaas.settings") as mock_settings:
            mock_settings.saturn_dpaas_home = ""
            mock_settings.saturn_build_file_home = ""
            mock_settings.dpaas_source_tar = ""
            mock_settings.dpaas_test_tar = ""
            init = DpaasInitializer()
            result = init.ensure_ready()

        assert result is True
        assert (dpaas_home / "zdpas" / "spark" / "jars").is_dir()

    def test_force_reinit_env_var(self, tmp_path, monkeypatch):
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        # Write sentinel to simulate already-initialised
        dpaas_home.mkdir(parents=True)
        (dpaas_home / ".saturn_dpaas_ready").touch()

        monkeypatch.setenv("DPAAS_FORCE_REINIT", "true")

        init = DpaasInitializer(
            dpaas_home=str(dpaas_home),
            source_tar=str(source_tar),
        )
        # force should be True from the env var
        assert init.force is True


class TestEnsureDpaasReadyConvenience:
    """Module-level ensure_dpaas_ready() wrapper."""

    def test_returns_false_when_nothing_configured(self, monkeypatch):
        monkeypatch.delenv("DPAAS_HOME", raising=False)
        monkeypatch.delenv("DPAAS_SOURCE_TAR", raising=False)

        with patch("dpaas.settings") as mock_settings:
            mock_settings.saturn_dpaas_home = ""
            mock_settings.saturn_build_file_home = ""
            mock_settings.dpaas_source_tar = ""
            mock_settings.dpaas_test_tar = ""
            result = ensure_dpaas_ready()

        assert result is False

    def test_returns_true_when_source_tar_and_dpaas_home_set(self, tmp_path, monkeypatch):
        dpaas_home = tmp_path / "dpaas"
        tars_dir = tmp_path / "tars"
        tars_dir.mkdir()
        source_tar = _make_source_tar(tars_dir)

        monkeypatch.setenv("DPAAS_HOME", str(dpaas_home))
        monkeypatch.setenv("DPAAS_SOURCE_TAR", str(source_tar))
        monkeypatch.delenv("BUILD_FILE_HOME", raising=False)
        monkeypatch.delenv("DPAAS_TEST_TAR", raising=False)
        monkeypatch.delenv("DPAAS_FORCE_REINIT", raising=False)

        with patch("dpaas.settings") as mock_settings:
            mock_settings.saturn_dpaas_home = ""
            mock_settings.saturn_build_file_home = ""
            mock_settings.dpaas_source_tar = ""
            mock_settings.dpaas_test_tar = ""
            result = ensure_dpaas_ready()

        assert result is True

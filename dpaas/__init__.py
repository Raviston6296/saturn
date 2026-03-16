"""
DPAAS one-time startup initializer.

On the new separate Saturn VM there is no GitLab runner and no pre-populated
DPAAS_HOME.  Saturn owns the full lifecycle:

  1. saturn.env supplies:
       DPAAS_HOME            — target directory (Saturn creates it if absent)
       BUILD_FILE_HOME       — directory that contains datastore.json
       DPAAS_SOURCE_TAR      — path to dpaas.tar.gz  (jars + sources)
       DPAAS_TEST_TAR        — path to dpaas_test.tar.gz (test resources)

  2. At application startup DpaasInitializer.ensure_ready() runs ONCE:
       a. Extract DPAAS_SOURCE_TAR  → DPAAS_HOME/
            → DPAAS_HOME/zdpas/spark/jars/   (compile classpath)
            → DPAAS_HOME/zdpas/spark/lib/    (additional jars)
       b. Extract DPAAS_TEST_TAR resources → DPAAS_HOME/zdpas/spark/resources/
            (test resource files needed at runtime)
       c. Copy BUILD_FILE_HOME/datastore.json
            → DPAAS_HOME/zdpas/spark/resources/datastore.json
            (not included in either tar; provided separately)
       d. Write a sentinel file so subsequent Saturn restarts skip re-extraction
          (unless DPAAS_FORCE_REINIT=true is set).

  3. The per-task 'setup' gate is removed from the gate pipeline — startup
     handles it once for all tasks on this VM.

Public API
----------
  DpaasInitializer.ensure_ready()   → bool   (idempotent — safe to call multiple times)
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from config import settings


# Sentinel file written inside DPAAS_HOME after successful init
_SENTINEL = ".saturn_dpaas_ready"


class DpaasInitializer:
    """
    One-time DPAAS_HOME population from tar files provided in saturn.env.

    Idempotent: if the sentinel already exists the extraction is skipped
    (unless DPAAS_FORCE_REINIT=true or force=True is passed).
    """

    def __init__(
        self,
        dpaas_home: str = "",
        source_tar: str = "",
        test_tar: str = "",
        build_file_home: str = "",
        force: bool = False,
    ):
        # Resolve from environment / settings when not given explicitly
        self.dpaas_home = Path(
            dpaas_home
            or os.environ.get("DPAAS_HOME", "").strip()
            or settings.saturn_dpaas_home.strip()
        )
        self.source_tar = Path(
            source_tar
            or os.environ.get("DPAAS_SOURCE_TAR", "").strip()
            or settings.dpaas_source_tar.strip()
        )
        self.test_tar_path = (
            test_tar
            or os.environ.get("DPAAS_TEST_TAR", "").strip()
            or settings.dpaas_test_tar.strip()
        )
        self.build_file_home = Path(
            build_file_home
            or os.environ.get("BUILD_FILE_HOME", "").strip()
            or settings.saturn_build_file_home.strip()
        ) if (
            build_file_home
            or os.environ.get("BUILD_FILE_HOME", "").strip()
            or settings.saturn_build_file_home.strip()
        ) else None

        force_env = os.environ.get("DPAAS_FORCE_REINIT", "").lower() in ("true", "1", "yes")
        self.force = force or force_env

    # ── Public API ──────────────────────────────────────────────────────────

    def ensure_ready(self) -> bool:
        """
        Ensure DPAAS_HOME is populated.  Returns True on success, False on error.

        Skips extraction when the sentinel file already exists (idempotent).
        Pass force=True or set DPAAS_FORCE_REINIT=true to always re-extract.
        """
        if not self.dpaas_home or str(self.dpaas_home) == ".":
            print(
                "⚠️  DPAAS startup init skipped: DPAAS_HOME is not set.\n"
                "   Set DPAAS_HOME in saturn.env or the system environment.\n"
                "   Example:  DPAAS_HOME=/opt/dpaas"
            )
            return False

        # Pin DPAAS_HOME (and BUILD_FILE_HOME) into os.environ so that every
        # subprocess spawned later — gate executor, test-gates route, validate_gates.sh
        # and the Java process it launches — all see the correct value.
        # This is necessary because pydantic-settings reads saturn.env into Settings
        # fields but does NOT populate os.environ.
        os.environ["DPAAS_HOME"] = str(self.dpaas_home)
        if self.build_file_home:
            os.environ["BUILD_FILE_HOME"] = str(self.build_file_home)

        sentinel = self.dpaas_home / _SENTINEL
        if sentinel.exists() and not self.force:
            print(f"✅ DPAAS_HOME already initialised ({self.dpaas_home}) — skipping tar extraction")
            print(f"   (Set DPAAS_FORCE_REINIT=true to force re-extraction)")
            return True

        print(f"\n{'━'*60}")
        print(f"🪐 Saturn DPAAS Startup Initialisation")
        print(f"{'━'*60}")
        print(f"  DPAAS_HOME:      {self.dpaas_home}")
        print(f"  DPAAS_SOURCE_TAR: {self.source_tar}")
        print(f"  DPAAS_TEST_TAR:  {self.test_tar_path or '(not set)'}")
        print(f"  BUILD_FILE_HOME: {self.build_file_home or '(not set)'}")
        print(f"{'━'*60}\n")

        try:
            self.dpaas_home.mkdir(parents=True, exist_ok=True)

            if not self._extract_source_tar():
                return False

            self._extract_test_tar()
            self._copy_datastore_json()
            self._ensure_log4j()

            sentinel.touch()
            print(f"\n✅ DPAAS_HOME ready at {self.dpaas_home}")
            print(f"{'━'*60}\n")
            return True

        except Exception as exc:
            print(f"\n❌ DPAAS initialisation failed: {exc}")
            return False

    # ── Private helpers ──────────────────────────────────────────────────────

    def _extract_source_tar(self) -> bool:
        """Extract dpaas.tar.gz into DPAAS_HOME.  Required — returns False if missing."""
        if not self.source_tar or not Path(self.source_tar).is_file():
            print(
                f"❌ DPAAS_SOURCE_TAR not found: {self.source_tar}\n"
                f"   Set DPAAS_SOURCE_TAR in saturn.env to the path of dpaas.tar.gz"
            )
            return False

        jars_dir = self.dpaas_home / "zdpas" / "spark" / "jars"
        if jars_dir.exists():
            shutil.rmtree(jars_dir.parent.parent)  # rm -rf DPAAS_HOME/zdpas

        print(f"  📦 Extracting source tar: {self.source_tar}")
        with tarfile.open(self.source_tar, "r:gz") as tf:
            tf.extractall(self.dpaas_home)  # noqa: S202 — tar contents are trusted (internal CI artifact)

        # Ensure app_blue dir exists for later jar packaging
        (self.dpaas_home / "zdpas" / "spark" / "app_blue").mkdir(parents=True, exist_ok=True)

        jar_count = len(list(jars_dir.glob("*.jar")))
        if jar_count == 0:
            print(f"❌ No jars found in {jars_dir} after extraction")
            return False

        print(f"  ✅ Extracted {jar_count} jars → {jars_dir}")
        return True

    def _extract_test_tar(self) -> None:
        """
        Extract DPAAS_TEST_TAR resources into DPAAS_HOME/zdpas/spark/resources/.

        Only the resources/ subtree from the test tar is copied — not the source
        files (those are compiled per-task from the worktree).  Optional; skipped
        silently when the tar is absent.
        """
        if not self.test_tar_path:
            return
        test_tar = Path(self.test_tar_path)
        if not test_tar.is_file():
            print(f"  ⚠️  DPAAS_TEST_TAR not found: {test_tar} — skipping test resource extraction")
            return

        resources_dest = self.dpaas_home / "zdpas" / "spark" / "resources"
        resources_dest.mkdir(parents=True, exist_ok=True)

        print(f"  📦 Extracting test tar resources: {test_tar}")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with tarfile.open(test_tar, "r:gz") as tf:
                tf.extractall(tmp_path)  # noqa: S202 — trusted CI artifact

            # Copy resources from the extracted tree
            copied = 0
            for candidate in [
                tmp_path / "zdpas" / "spark" / "resources",
                tmp_path / "resources",
            ]:
                if candidate.is_dir():
                    for item in candidate.iterdir():
                        dest = resources_dest / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)
                        copied += 1
                    break

        print(f"  ✅ Test tar: {copied} resource entries → {resources_dest}")

    def _copy_datastore_json(self) -> None:
        """
        Copy BUILD_FILE_HOME/datastore.json → DPAAS_HOME/zdpas/spark/resources/.

        The datastore.json is not included in either tar; it is generated by CI/CD
        and stored separately in BUILD_FILE_HOME.  Optional; skipped when absent.
        """
        if not self.build_file_home:
            return
        src = self.build_file_home / "datastore.json"
        if not src.is_file():
            print(f"  ⚠️  datastore.json not found at {src} — skipping")
            return

        dest = self.dpaas_home / "zdpas" / "spark" / "resources" / "datastore.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(f"  ✅ Copied datastore.json → {dest}")

    def _ensure_log4j(self) -> None:
        """Write a minimal log4j config if one is not already present."""
        conf_dir = self.dpaas_home / "zdpas" / "spark" / "conf"
        conf_dir.mkdir(parents=True, exist_ok=True)
        log4j = conf_dir / "log4j-local.properties"
        if log4j.exists():
            return
        log4j.write_text(
            "log4j.rootLogger=WARN, console\n"
            "log4j.appender.console=org.apache.log4j.ConsoleAppender\n"
            "log4j.appender.console.layout=org.apache.log4j.PatternLayout\n"
            "log4j.appender.console.layout.ConversionPattern=%d{HH:mm:ss} %-5p %c{1} - %m%n\n"
        )
        print(f"  ✅ Wrote default log4j config → {log4j}")

    # ── Module-level convenience ─────────────────────────────────────────────


def ensure_dpaas_ready(force: bool = False) -> bool:
    """
    Module-level convenience wrapper — create a DpaasInitializer and run it.

    Called from server/app.py lifespan and main.py CLI entry point.
    """
    return DpaasInitializer(force=force).ensure_ready()

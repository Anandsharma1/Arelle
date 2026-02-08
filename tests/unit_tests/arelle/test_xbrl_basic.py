"""Tests for XBRL loading with WebCache patching for relative schema resolution.

These tests demonstrate monkey-patching Arelle's WebCache.getfilename() to
handle relative schema references inside taxonomy ZIP packages. This patching
approach works with any Arelle installation (fork or official PyPI package).
"""

import logging
import os
import zipfile
from pathlib import Path

import pytest

import arelle.WebCache
from arelle import Cntlr
from arelle.api.Session import Session
import arelle.PackageManager
from arelle.PackageManager import addPackage, rebuildRemappings
from arelle.RuntimeOptions import RuntimeOptions

logger = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).parent / "resources" / "xbrl"

XBRL_FILE = RESOURCES_DIR / "Zydus_INTEGRATED_FILING_INDAS_1444664_20052025064321_WEB.xml"
TAXONOMY_PACKAGE = RESOURCES_DIR / "Taxonomy Integrated filing finance (IndAS).zip"


def _skip_if_missing():
    """Skip test if resource files are not present."""
    if not XBRL_FILE.exists():
        pytest.skip(f"XBRL test file not found: {XBRL_FILE}")
    if not TAXONOMY_PACKAGE.exists():
        pytest.skip(f"Taxonomy package not found: {TAXONOMY_PACKAGE}")


class TestXbrlWithPatchedWebCache:
    """Tests that demonstrate WebCache patching for relative schema resolution."""

    def test_instance_level_patch_with_cntlr(self):
        """Patch WebCache on a controller instance to resolve relative schemas from taxonomy ZIPs."""
        _skip_if_missing()

        controller = Cntlr.Cntlr(logFileName="logToPrint")
        controller.webCache.workOffline = True

        new_package = None
        for pkg in [str(TAXONOMY_PACKAGE)]:
            new_package = addPackage(controller, pkg)
        if new_package:
            rebuildRemappings(controller)

        # Patch the instance-level getfilename to search taxonomy ZIPs
        original_getfilename = controller.webCache.getfilename

        def patched_getfilename(url, base=None, reload=False, checkModifiedTime=False,
                                normalize=False, filenameOnly=False, allowTransformation=True):
            if url and not url.startswith(("http://", "https://")) and url.endswith((".xsd", ".xml")):
                basename = os.path.basename(url)
                config = arelle.PackageManager.packagesConfig
                for _map_from, map_to in (config or {}).get("remappings", {}).items():
                    if map_to.endswith(".zip/") or map_to.endswith(".zip"):
                        zip_path = map_to.rstrip("/")
                        try:
                            with zipfile.ZipFile(zip_path, "r") as zf:
                                for entry in zf.namelist():
                                    if os.path.basename(entry) == basename:
                                        return f"{zip_path}/{entry}"
                        except (zipfile.BadZipFile, OSError):
                            continue

            return original_getfilename(url, base, reload, checkModifiedTime,
                                        normalize, filenameOnly, allowTransformation)

        controller.webCache.getfilename = patched_getfilename

        model_xbrl = controller.modelManager.load(str(XBRL_FILE))

        assert model_xbrl is not None
        assert model_xbrl.modelDocument is not None
        assert len(model_xbrl.facts) > 0, "No facts loaded — schema resolution may have failed"

    def test_class_level_patch_with_session(self):
        """Patch WebCache at the class level before Session creation.

        This is the approach used by webcache_patch.py in xbrl_analyzer
        and works with any Arelle installation (official PyPI or fork).
        """
        _skip_if_missing()

        original_getfilename = arelle.WebCache.WebCache.getfilename

        def patched_getfilename(self, url, base=None, reload=False, checkModifiedTime=False,
                                normalize=False, filenameOnly=False, allowTransformation=True):
            if url and not url.startswith(("http://", "https://")) and url.endswith((".xsd", ".xml")):
                basename = os.path.basename(url)
                try:
                    config = arelle.PackageManager.packagesConfig
                    if config and "remappings" in config:
                        for _map_from, map_to in config.get("remappings", {}).items():
                            if map_to.endswith(".zip/") or map_to.endswith(".zip"):
                                zip_path = map_to.rstrip("/")
                                try:
                                    with zipfile.ZipFile(zip_path, "r") as zf:
                                        for entry in zf.namelist():
                                            if os.path.basename(entry) == basename:
                                                return f"{zip_path}/{entry}"
                                except (zipfile.BadZipFile, OSError):
                                    continue
                except Exception:
                    logger.debug("Error accessing packagesConfig", exc_info=True)

            return original_getfilename(self, url, base, reload, checkModifiedTime,
                                        normalize, filenameOnly, allowTransformation)

        try:
            arelle.WebCache.WebCache.getfilename = patched_getfilename

            options = RuntimeOptions(
                entrypointFile=str(XBRL_FILE),
                internetConnectivity="offline",
                keepOpen=True,
                packages=[str(TAXONOMY_PACKAGE)],
            )

            with Session() as session:
                session.run(options)
                models = session.get_models()

                assert len(models) > 0, f"Failed to load any models from {XBRL_FILE}"

                model_xbrl = models[0]
                assert model_xbrl is not None
                assert len(model_xbrl.facts) > 0, f"No facts found in {XBRL_FILE}"
        finally:
            arelle.WebCache.WebCache.getfilename = original_getfilename

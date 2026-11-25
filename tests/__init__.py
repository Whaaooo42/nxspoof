"""
tests - unit tests for the nxspoof package (run from anywhere: python3 -B tools/run_tests.py; no sudo needed).

Safety net, set before any test runs: the tests can never reach your real files. NXSPOOF's data folder
(~/Library/Application Support/NXSPOOF: settings.json, logs/) and the project folder main() falls back to are both
swapped for empty temporary folders, removed at the end.
golden(key): the expected outputs recorded at the cutoff (tests/fixtures/golden.json), for the tests that pin exact answers.
"""

import atexit
import json
import os
import shutil
import tempfile

from nxspoof.app import main as _main
from nxspoof.macos import user as _user

SAFE_DATA = tempfile.mkdtemp(prefix="nxspoof-test-data-")
SAFE_PROJECT = tempfile.mkdtemp(prefix="nxspoof-test-project-")
REAL_DATA_FOLDER = _user.data_folder         # kept for the test of data_folder() itself (it only computes the path)
_user.data_folder = lambda: SAFE_DATA
_main.PROJECT_DIR = SAFE_PROJECT
atexit.register(shutil.rmtree, SAFE_DATA, True)
atexit.register(shutil.rmtree, SAFE_PROJECT, True)

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden.json")

# An expected output recorded at the cutoff (tests/fixtures/golden.json: what the code answered on 2026-11-16), by key.
def golden(key):
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)[key]

"""
nxspoof.core.paths - where the project lives, so every path is relative to the nx-spoof folder.

The folder can be moved anywhere: PROJECT_DIR is worked out from this file's location
(nx-spoof/nxspoof/core/paths.py -> nx-spoof/).
"""

import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A path inside the project folder, e.g. project_path("logs") or project_path("lists", "blocklist.txt").
def project_path(*parts):
    return os.path.join(PROJECT_DIR, *parts)

# A path as shown to people: relative to the project folder when it's inside it, absolute otherwise.
def display_path(path):
    path = os.path.abspath(path)
    rel = os.path.relpath(path, PROJECT_DIR)
    return path if rel.startswith("..") else rel

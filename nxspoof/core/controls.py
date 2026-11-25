"""
nxspoof.core.controls - the registry of controls (dashboard buttons and other commands).

Features register their own controls; the dashboard lists the ones available right now and calls
them by name. A handler gets a payload dict and returns a result dict (or None for {"ok": True}).
Bad input or an impossible request raises ControlError, which the dashboard turns into HTTP 400
with the reason; anything else is a bug (HTTP 500).
"""

import threading

# Raised by a control for bad input or a request that can't be done right now (shown to the user).
class ControlError(ValueError):
    pass

# Name -> (handler, availability check).
class Controls(object):
    # Start with no controls.
    def __init__(self):
        self._controls = {}
        self._lock = threading.Lock()

    # Register a control; available() (optional) says whether it can be used right now.
    def add(self, name, handler, available=None):
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("control names are letters, digits and underscores: %r" % name)
        with self._lock:
            if name in self._controls:
                raise ValueError("control %r is already registered" % name)
            self._controls[name] = (handler, available)

    # Names of the controls that can be used right now (sorted).
    def available(self):
        with self._lock:
            items = list(self._controls.items())
        out = []
        for name, (_, check) in items:
            try:
                if check is None or check():
                    out.append(name)
            except Exception:
                pass
        return sorted(out)

    # True if a control with this name is registered (available or not).
    def has(self, name):
        with self._lock:
            return name in self._controls

    # Run a control; raises KeyError if unknown, ControlError if it can't run now or the input is bad.
    def call(self, name, payload=None):
        with self._lock:
            entry = self._controls.get(name)
        if entry is None:
            raise KeyError(name)
        handler, check = entry
        if check is not None and not check():
            raise ControlError("'%s' isn't available right now" % name)
        result = handler(dict(payload or {}))
        return result if result is not None else {"ok": True}

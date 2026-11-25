"""
nxspoof.macos.shell - running a command on this Mac, the one way every other part does it.

  run(cmd, text=None) -> (exit code, output with errors appended); text goes to the command's input (scutil);
                         a missing program is code 127, never an exception
  start(cmd) -> the running process, at once (its output collected); finish(it) -> (exit code, output) when it ends

Code outside macos/ never starts programs itself (tests/test_guardrails.py checks it): it asks macos/,
which can be handed a fake runner in tests. Never logs.
"""

import subprocess

# Run a command and collect its output -> (exit code, output); text: what to type into it, if anything.
def run(cmd, text=None):
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, input=text)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except OSError as e:
        return 127, str(e)

# Start a command without waiting for it (its output and errors collected for finish()).
def start(cmd):
    return subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)

# Wait for a command started with start() -> (exit code, output).
def finish(process):
    out, _ = process.communicate()
    return process.returncode, out or ""

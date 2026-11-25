"""
nxspoof.app.signals - Ctrl+C, a closed Terminal window and SIGTERM all become one stop request.

The handlers only record the request (Context.request_stop); the main thread notices it and runs the
ordered shutdown. Nothing is logged or cleaned up inside a signal handler, and repeats (Terminal and
sudo may both send one when the window closes) change nothing, so the shutdown can't be cut short.
"""

import signal

STOP_SIGNALS = (signal.SIGINT, signal.SIGHUP, signal.SIGTERM)

# Route the stop signals to ctx.request_stop, unblocked (a launcher may hand them over blocked: macOS's password window);
# returns the previous handlers (for restore()).
def install(ctx):
    previous = {}
    signal.pthread_sigmask(signal.SIG_UNBLOCK, STOP_SIGNALS)

    # One stop signal: Ctrl+C is "ctrl-c", the others are named (e.g. "SIGTERM").
    def handler(signum, _frame):
        ctx.request_stop("ctrl-c" if signum == signal.SIGINT else signal.Signals(signum).name)
    for sig in STOP_SIGNALS:
        previous[sig] = signal.signal(sig, handler)
    return previous

# Put back the handlers install() replaced.
def restore(previous):
    for sig, old in previous.items():
        signal.signal(sig, old if old is not None else signal.SIG_DFL)

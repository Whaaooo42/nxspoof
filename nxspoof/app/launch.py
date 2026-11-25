"""
nxspoof.app.launch - the dashboard on its own, as app/main.py starts it (nx_spoof.py dashboard; nx_spoof.py start is app/start.py).

  center_command  the control center (app/center.py): the dashboard without a run, no admin rights
"""

from nxspoof.app import cli, reporting
from nxspoof.app.commands import load_store
from nxspoof.settings.store import SettingsError

# The control center: the dashboard on its own (app/center.py). prepared: what preparing the folders did.
def center_command(args, system, places, stream=None, prepared=()):
    from nxspoof.app.center import Center
    try:
        store, message = load_store(args, places, on_saved=system.give_to_user)
    except SettingsError as e:
        print(str(e))
        return 1
    center = Center(store, message, system, places, stream, ignored=cli.given_options(args, keep=("--settings",)), prepared=prepared)
    reporting.attach(center)
    return center.run()

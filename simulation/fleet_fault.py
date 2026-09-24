"""Toggle simulated heartbeat loss for one logical fleet device.

This changes only simulator-owned state: the named device is added to (or removed
from) the hold file that `simulation.fleet_scale --serve` reads each round. It does
not call any "fail this device" API - the control plane learns about the outage the
same way it learns about a real one, by the heartbeat stopping and the server-side
liveness window elapsing.
"""
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--hold-file', default='var/fleet-hold.json')
parser.add_argument('--device', required=True, help='logical device name, e.g. device-07421')
parser.add_argument('--recover', action='store_true')
arguments = parser.parse_args()

path = Path(arguments.hold_file)
held = set()
if path.is_file():
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {'down': []}
    held = set(data.get('down', []) if isinstance(data, dict) else data)
if arguments.recover:
    held.discard(arguments.device)
else:
    held.add(arguments.device)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({'down': sorted(held)}, indent=2) + '\n')
print(('Heartbeat resumed for ' if arguments.recover else 'Heartbeat withheld for ')
      + arguments.device + ' (%d device(s) held)' % len(held))

"""Rolling per-lap timing history.

The document MultiViewer serves carries only the *current* lap time for each
car -- ``LapSeries`` in this feed is positions, not times -- so anything that
needs a trend has to be remembered rather than read.  Every snapshot appends
the laps it has not seen before.

The file is shared by the gateway, the watcher and the CLI: whichever of them
looked last, the next one sees the same history.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .model import Session

#: Laps kept per driver -- a full race on one compound, cheap on disk.
KEEP = 120
#: A history this old belongs to a different session whatever the key says.
STALE = 6 * 3600


def path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "state" / "pitwall-laps.json"


def read(session_key=None) -> dict:
    """Stored history, or an empty one when it belongs to another session."""
    try:
        payload = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"session": session_key, "laps": {}}
    if session_key is not None and str(payload.get("session")) != str(session_key):
        return {"session": session_key, "laps": {}}
    if time.time() - float(payload.get("updated") or 0) > STALE:
        return {"session": session_key, "laps": {}}
    payload.setdefault("laps", {})
    return payload


def record(session: Session) -> dict:
    """Append every lap the store has not seen. Returns the updated history."""
    data = read(session.key)
    laps = data["laps"]
    changed = False
    for driver in session.drivers:
        if not driver.last_lap or driver.laps <= 0:
            continue
        entries = laps.setdefault(driver.num, [])
        if entries and driver.laps <= int(entries[-1].get("lap") or 0):
            continue          # already seen, or the session replayed backwards
        entries.append({"lap": int(driver.laps), "t": round(float(driver.last_lap), 3)})
        del entries[:-KEEP]
        changed = True
    if changed:
        data["session"] = session.key
        data["updated"] = time.time()
        _save(data)
    return data


def _save(data: dict) -> None:
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass
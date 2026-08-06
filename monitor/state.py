"""The two artefacts that must survive between cycles.

The agent is stateless between runs, so two things are persisted:
  (a) the previous cycle's snapshot, keyed by (leg_id, hotel_id);
  (b) the credit counter for the current month.

Losing (a) makes every hotel look new. Losing (b) is treated as quota
exhaustion, per the operating rules -- with one carve-out: a genuine cold start
(no ledger, no snapshot, and no history) is not a loss, it is a first run, and
is allowed to begin at zero.

History is append-only. Nothing here ever rewrites a past line.
"""

import json
import os
from datetime import datetime, timezone

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
LEDGER_PATH = os.path.join(STATE_DIR, "credits.json")
SNAPSHOT_PATH = os.path.join(STATE_DIR, "snapshot.json")
HISTORY_PATH = os.path.join(STATE_DIR, "history.ndjson")
RUNS_PATH = os.path.join(STATE_DIR, "runs.ndjson")


def _month_key(ts=None):
    return (ts or datetime.now(timezone.utc)).strftime("%Y-%m")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _atomic_write(path, payload):
    """Write via a temp file + rename so a crash mid-write cannot truncate
    the ledger and make the next cycle think state was lost."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class CreditLedger:
    """Per-month credit counter, flushed after every single request."""

    def __init__(self, monthly_credits):
        self.monthly_credits = monthly_credits
        self.month = _month_key()
        self.state_lost = False

        data = _read_json(LEDGER_PATH, None)
        if data is None:
            cold_start = not os.path.exists(SNAPSHOT_PATH) and not os.path.exists(HISTORY_PATH)
            if cold_start:
                self.used = 0
            else:
                # Counter gone but other state present -> assume the worst.
                self.used = monthly_credits
                self.state_lost = True
        elif data.get("month") != self.month:
            self.used = 0          # new month, credits reset and do not accrue
        else:
            self.used = int(data.get("used", 0))

        self.used_this_run = 0

    @property
    def remaining(self):
        return max(0, self.monthly_credits - self.used)

    def charge(self, n=1):
        self.used += n
        self.used_this_run += n
        self.flush()

    def flush(self):
        _atomic_write(LEDGER_PATH, {
            "month": self.month,
            "used": self.used,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })


def load_snapshot():
    """Previous cycle keyed by "leg_id:hotel_id"."""
    return _read_json(SNAPSHOT_PATH, {})


def save_snapshot(snapshot):
    _atomic_write(SNAPSHOT_PATH, snapshot)


def append_history(rows):
    """Append-only observation log; feeds the 7-day moving average."""
    if not rows:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_run(run_obj):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RUNS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(run_obj, ensure_ascii=False) + "\n")


def load_history():
    rows = []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue      # never let one bad line kill a cycle
    except FileNotFoundError:
        pass
    return rows

"""How many episodes still need a Claude read (so the reading jobs can skip spinning up when there are none).
    python pending.py liquidity|events   -> prints a number"""
import json, sys
from common import KINDS, episodes, kdir


def todo(out_dir, ids):
    n = 0
    for i in ids:
        p = out_dir / f"{i}.json"
        if not p.exists():
            n += 1
        else:
            j = json.loads(p.read_text())
            n += bool(j.get("error")) or str(j.get("status", "")).startswith("error")
    return n


if sys.argv[1] == "events":
    print(todo(kdir("crash", "events"), [f"{c['cik']}_{c['date']}" for c in episodes("crash")]))
else:
    print(sum(todo(kdir(k, "liquidity"), [p.stem for p in kdir(k, "inputs").glob("*.json")
                                          if json.loads(p.read_text()).get("balance")]) for k in KINDS))

#!/usr/bin/env python3
"""A last_run.json megjelolese, ha a feed generalas sikerult, de a
publikalas (git push) nem. Igy a riport nem mutat "ok": true-t egy
olyan futasnal, ami valojaban elbukott."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "feed", "last_run.json")

try:
    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}

data["ok"] = False
data["stage_failed"] = "publish"
data["error"] = ("A feed generalas sikeres volt, de a publikalas (git push) "
                 "nem sikerult. A linken a korabbi, meg jo feed maradt kint.")

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)

print("last_run.json megjelolve: publikalas elbukott")

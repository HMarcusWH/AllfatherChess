#!/usr/bin/env python3
"""Run the frozen G3-v2 qualifier against the isolated ENGINE-OPT candidate overlay."""
from __future__ import annotations
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
env=os.environ.copy()
env.update({
 "ALLFATHER_G3_SELECTION":"qualification/engine-opt-v2-candidate-selection.json",
 "ALLFATHER_G3_REFERENCE":"config/allfather.online-engine-opt-v2.candidate.json",
 "ALLFATHER_G3_CONFIG":"config/allfather.online-hybrid-v2.candidate.validation.json",
 "ALLFATHER_G3_RESULT":"build/test-results/online-hybrid-v2-candidate",
})
os.execvpe(sys.executable,[sys.executable,str(ROOT/"scripts/qualify-online-hybrid-v2.py"),*sys.argv[1:]],env)

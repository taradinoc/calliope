"""Internal bridge to the existing SGDK build pipeline."""
from pathlib import Path
import sys
import build
stage=Path(sys.argv.pop(1)).resolve()
build.main(stage,stage/'bank.json')

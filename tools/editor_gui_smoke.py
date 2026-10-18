"""Exercise real GUI worker/audio integration; optional offscreen Qt platform."""
from pathlib import Path
import argparse
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
local=ROOT/'out/editor-deps'
if local.is_dir():sys.path.insert(0,str(local))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase,QFont
from calliope.editor.window import EditorWindow

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bank',type=Path,default=ROOT/'examples/bank.json')
parser.add_argument('--mode',choices=('z80','hybrid'),default='z80')
parser.add_argument('--song-index',type=int,default=4)
parser.add_argument('--timeout',type=int,default=120)
args=parser.parse_args()
app=QApplication([])
if sys.platform=='win32':
    app.setFont(QFont('Segoe UI',10))
window=EditorWindow();window.open_path(args.bank)
window.mode.setCurrentIndex(int(args.mode=='hybrid'))
window.browser.setCurrentItem(window.browser.topLevelItem(0).child(0).child(args.song_index));window.show();window.play_song()
result={'ok':False};triggered=False
def finish():
    result.update(preview=window.preview_label.text(),cpu_load=window.cpu_load.text(),status=getattr(window,'last_status',{}),diagnostics=window.diagnostics.toPlainText()[:1000])
    window.grab().save(str(ROOT/'out/editor-playing.png'))
    (ROOT/'out/editor-gui-smoke.json').write_text(json.dumps(result,indent=2))
    window.close();app.quit()
def poll():
    global triggered
    if window.player and getattr(window,'last_status',{}).get('playing') and not triggered:
        triggered=True
        if window.sfx.count():window.sfx.setCurrentIndex(min(3,window.sfx.count()-1));window.trigger_sfx()
        QTimer.singleShot(2000,success)
def success():
    state=getattr(window,'last_status',{})
    result['ok']=bool(window.player and state.get('playing') and state.get('m68k_load') is not None and '%' in window.cpu_load.text());finish()
timer=QTimer();timer.setInterval(100);timer.timeout.connect(poll);timer.start()
QTimer.singleShot(args.timeout*1000,finish);app.exec();print(json.dumps(result))
sys.exit(0 if result['ok'] else 1)

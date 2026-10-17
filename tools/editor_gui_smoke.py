"""Exercise real GUI worker/audio integration; optional offscreen Qt platform."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
local=ROOT/'out/editor-deps'
if local.is_dir():sys.path.insert(0,str(local))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase,QFont
from calliope.editor.window import EditorWindow

app=QApplication([])
if sys.platform=='win32':
    app.setFont(QFont('Segoe UI',10))
window=EditorWindow();window.open_path(ROOT/'examples/bank.json')
window.browser.setCurrentItem(window.browser.topLevelItem(0).child(0).child(4));window.show();window.play_song()
result={'ok':False};triggered=False
def finish():
    result.update(preview=window.preview_label.text(),status=getattr(window,'last_status',{}),diagnostics=window.diagnostics.toPlainText()[:1000])
    window.grab().save(str(ROOT/'out/editor-playing.png'))
    (ROOT/'out/editor-gui-smoke.json').write_text(json.dumps(result,indent=2))
    window.close();app.quit()
def poll():
    global triggered
    if window.player and getattr(window,'last_status',{}).get('playing') and not triggered:
        triggered=True;window.sfx.setCurrentIndex(3);window.trigger_sfx();QTimer.singleShot(2000,success)
def success():
    result['ok']=bool(window.player and getattr(window,'last_status',{}).get('playing'));finish()
timer=QTimer();timer.setInterval(100);timer.timeout.connect(poll);timer.start()
QTimer.singleShot(30000,finish);app.exec();print(json.dumps(result))
sys.exit(0 if result['ok'] else 1)

"""Optional Qt checks for ordered authoring and safe invalid-document display."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
if (ROOT/'out/editor-deps').exists():sys.path.insert(0,str(ROOT/'out/editor-deps'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from calliope.editor.window import EditorWindow
    QT=True
except ImportError:QT=False

@unittest.skipUnless(QT,'PySide6 not installed')
class EditorGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):self.window=EditorWindow()

    def tearDown(self):
        self.window.document.saved=self.window.document.data
        self.window.json.document().setModified(False)
        self.window.close();self.app.processEvents()

    def test_sample_sharing_counts_audio_even_with_different_descriptors(self):
        sample = dict(category='songs', resource_id=1, resource_name='First',
                      sample=0, sample_name='Kick', offset=128, descriptor=512,
                      bytes=256, source_rate=22050, gain=1.0, loop=None, source='song.fur')
        self.window.show_sample_sharing({'music_samples': [sample,
            dict(sample, resource_id=2, resource_name='Second', descriptor=528)]})
        table = self.window.samples_table
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual([table.item(i, 6).text() for i in range(2)], ['2', '2'])
        self.assertEqual(table.item(0, 5).text(), table.item(1, 5).text())
        self.assertNotEqual(table.item(0, 7).text(), table.item(1, 7).text())
        self.window.show_sample_sharing({'music_samples': []})
        self.assertEqual(table.rowCount(), 0)

    def test_cpu_load_display_and_stop(self):
        status = dict(owners=[0]*9, effects=[], pcm_owner=0, tick=0,
                      fault=0, underruns=0, ack=0, queued=False, m68k_load=12.34)
        self.window.show_status(status)
        self.assertEqual(self.window.cpu_load.text(), '68000 load: 12.3%')
        status['m68k_load'] = 0
        self.window.show_status(status)
        self.assertEqual(self.window.cpu_load.text(), '68000 load: 0.0%')
        del status['m68k_load']
        self.window.show_status(status)
        self.assertEqual(self.window.cpu_load.text(), '68000 load: unavailable')
        self.window.stop_preview()
        self.assertEqual(self.window.cpu_load.text(), '68000 load: —')

    def test_json_and_forms_share_history(self):
        value={'version':1,'songs':[{'id':1,'name':'song','source':'missing.fur','voices':[]}],'extension':{'a':1}}
        self.window.json.setPlainText(json.dumps(value));self.assertTrue(self.window.apply_json())
        self.window.browser.setCurrentItem(self.window.browser.topLevelItem(0).child(0).child(0))
        edited=dict(value['songs'][0],pcm_gain=.5);self.window.properties_changed(edited)
        self.assertEqual(self.window.document.data['songs'][0]['pcm_gain'],.5)
        self.assertEqual(self.window.document.data['extension'],{'a':1})
        self.window.history(-1);self.assertNotIn('pcm_gain',self.window.document.data['songs'][0])

    def test_invalid_fields_can_be_shown_for_validation(self):
        value={'version':1,'songs':[{'id':1,'name':'song','source':'missing.fur','arrangements':7}],
               'sfx':[{'id':2,'name':17,'source':'missing.wav','priority':'invalid'}]}
        self.window.replace(value)
        self.window.browser.setCurrentItem(self.window.browser.topLevelItem(0).child(0).child(0))
        self.assertEqual(self.window.document.data,value)

    def test_property_forms_have_real_controls_and_preserve_unknown_fields(self):
        from PySide6.QtWidgets import QLineEdit,QComboBox,QDoubleSpinBox,QTreeWidget
        value={'version':1,'songs':[{'id':1,'name':'song','source':'missing.fur','voices':[{'name':'lead','channel':0,'slot':'fm0','custom':9}],'custom':{'keep':True}}]}
        self.window.replace(value);self.window.browser.setCurrentItem(self.window.browser.topLevelItem(0).child(0).child(0))
        form=self.window.properties
        self.assertIsInstance(form.controls['name'],QLineEdit)
        self.assertIsInstance(form.controls['subsong'],QComboBox)
        self.assertIsInstance(form.controls['pcm_gain'],QDoubleSpinBox)
        self.assertEqual(form.findChildren(QTreeWidget),[])
        form.controls['name'].setText('renamed');form.controls['name'].editingFinished.emit()
        self.assertEqual(self.window.document.data['songs'][0]['name'],'renamed')
        self.assertEqual(self.window.document.data['songs'][0]['custom'],{'keep':True})
        form.voice_table.cellWidget(0,2).setCurrentIndex(1)
        self.assertEqual(self.window.document.data['songs'][0]['voices'][0]['slot'],'fm1')
        self.assertEqual(self.window.document.data['songs'][0]['voices'][0]['custom'],9)

    def test_progress_is_visible_during_preparation(self):
        self.window.set_progress('Compiling music 1/3: song…')
        self.assertIn('Compiling music',self.window.progress_label.text())
        self.assertFalse(self.window.progress_bar.isHidden());self.assertFalse(self.window.cancel_job.isHidden())
        self.window.set_progress(None);self.assertTrue(self.window.progress_bar.isHidden())

    def test_deleting_last_arrangement_and_marker_restores_valid_song(self):
        from calliope.editor.services import validate_document
        value={'version':1,'songs':[{'id':1,'name':'melody','source':'melody232.fur',
               'voices':[{'name':'lead','channel':'melody','slot':'fm0'}],'external_commands':'passthrough'}]}
        self.window.document.base_dir=ROOT/'examples'
        self.window.replace(value);self.window.browser.setCurrentItem(self.window.browser.topLevelItem(0).child(0).child(0))
        form=self.window.properties
        form.add_member('arrangements');form.set('initial_arrangement','arrangement_1')
        form.add_marker();form.remove_member('arrangements',0);form.remove_index('markers',0)
        song=self.window.document.data['songs'][0]
        self.assertNotIn('arrangements',song);self.assertNotIn('initial_arrangement',song);self.assertNotIn('markers',song)
        self.assertEqual(validate_document(self.window.document.data,ROOT/'examples'),[])
        self.window.history(-1);self.assertEqual(len(self.window.document.data['songs'][0]['markers']),1)

if __name__=='__main__':unittest.main()

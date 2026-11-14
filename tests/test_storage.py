import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import main

class ProjectStorageTests(unittest.TestCase):
    def test_project_reader_does_not_overlap_atomic_replace(self):
        with tempfile.TemporaryDirectory(dir='data') as folder,patch.object(main,'DATA',Path(folder)):
            pid='f066ce8d-732d-4d31-91aa-77687dac3892'
            main.save(pid,{'id':pid,'title':'first','scenes':[]})
            target=main.project_dir(pid)/'project.json';entered=threading.Event();release=threading.Event();done=threading.Event()
            original=Path.read_text
            def slow_read(path,*args,**kwargs):
                if path==target: entered.set();release.wait(2)
                return original(path,*args,**kwargs)
            with patch.object(Path,'read_text',slow_read):
                reader=threading.Thread(target=main.read_project,args=(pid,));reader.start();self.assertTrue(entered.wait(1))
                writer=threading.Thread(target=lambda:(main.save(pid,{'id':pid,'title':'second','scenes':[]}),done.set()));writer.start()
                try:
                    self.assertFalse(done.wait(.15),'replace must wait until the active read closes')
                finally:
                    release.set();reader.join(2);writer.join(2)
            self.assertTrue(done.is_set());self.assertEqual(main.read_project(pid)['title'],'second')

    def test_transient_windows_sharing_violation_is_retried_without_losing_old_project(self):
        with tempfile.TemporaryDirectory(dir='data') as folder,patch.object(main,'DATA',Path(folder)):
            pid='f066ce8d-732d-4d31-91aa-77687dac3892'
            main.save(pid,{'id':pid,'title':'first','scenes':[]})
            original=Path.replace;attempts=[]
            def retry_once(path,target):
                attempts.append(1)
                if len(attempts)==1:
                    self.assertEqual(main.read_project(pid)['title'],'first')
                    raise PermissionError(13,'file is temporarily held')
                return original(path,target)
            with patch.object(Path,'replace',retry_once):main.save(pid,{'id':pid,'title':'second','scenes':[]})
            self.assertEqual(len(attempts),2);self.assertEqual(main.read_project(pid)['title'],'second')

if __name__=='__main__':unittest.main()

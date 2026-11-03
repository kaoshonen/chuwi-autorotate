"""Temporary sleep selection with a durable, independently recoverable journal."""
import json
import os
import stat
from pathlib import Path
import re
from .config import atomic


class SleepTrial:
    def __init__(self, mode_path=Path('/sys/power/mem_sleep'), journal=Path('/var/lib/chuwi-autorotate/sleep.json')):
        self.mode_path, self.journal = Path(mode_path), Path(journal)

    def selected(self):
        match = re.search(r'\[(s2idle|shallow|deep)\]',self.mode_path.read_text())
        if not match: raise RuntimeError('Selected sleep mode is unreadable')
        return match[1]

    def prepare(self):
        if self.journal.exists(): raise RuntimeError('Previous sleep trial must be restored first')
        previous=self.selected()
        if 'deep' not in self.mode_path.read_text().replace('[','').replace(']','').split():
            raise RuntimeError('Firmware does not expose deep sleep')
        atomic(self.journal,json.dumps({'version':1,'previous':previous}).encode())
        try: self.select('deep')
        except BaseException:
            self.restore()
            raise

    def select(self, mode):
        self.mode_path.write_text(mode)
        if self.selected() != mode:
            raise RuntimeError('Sleep mode readback did not match; recovery journal retained')

    def restore(self):
        if not self.journal.exists(): return
        info=self.journal.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Unsafe sleep recovery journal')
        record=json.loads(self.journal.read_text())
        if record.get('version') != 1 or record.get('previous') not in ('s2idle','shallow','deep'):
            raise RuntimeError('Invalid sleep recovery journal')
        self.select(record['previous'])
        self.journal.unlink()

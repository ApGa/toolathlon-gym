import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from task_process import process_group_is_alive


@unittest.skipUnless(sys.platform == "linux", "Linux process-group cleanup")
class ProcessGroupTest(unittest.TestCase):
    def test_zombie_is_not_reported_as_running(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        try:
            for _ in range(200):
                state = Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                if state == "Z":
                    break
                time.sleep(.01)
            self.assertEqual(state, "Z")
            os.killpg(proc.pid, 0)  # The previous cleanup check reports alive.
            self.assertFalse(process_group_is_alive(proc.pid))
        finally:
            proc.wait(timeout=5)

    def test_live_member_is_detected_after_group_leader_exits(self):
        leader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], process_group=0)
        member = None
        try:
            member = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], process_group=leader.pid)
            leader.terminate()
            leader.wait(timeout=5)
            self.assertTrue(process_group_is_alive(leader.pid))
            member.terminate()
            member.wait(timeout=5)
            self.assertFalse(process_group_is_alive(leader.pid))
        finally:
            for proc in (member, leader):
                if proc is not None:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=5)

    def test_special_group_ids_are_rejected(self):
        for pgid in (0, -1):
            with self.assertRaises(ValueError):
                process_group_is_alive(pgid)

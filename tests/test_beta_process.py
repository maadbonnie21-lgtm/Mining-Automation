from __future__ import annotations

import subprocess
import sys

import pytest

from mining_automation.beta_process import ChildJob, InstanceLease, wait_for_parent_gate


def test_second_launcher_cannot_acquire_live_lease(tmp_path):
    first, second = InstanceLease(tmp_path / "lease"), InstanceLease(tmp_path / "lease")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another beta launcher"):
            second.acquire()
    finally:
        first.close()
    second.acquire()
    second.close()
    assert (tmp_path / "lease").exists()  # The inode/path is never deleted/replaced.


def test_parent_gate_must_be_positively_acknowledged(tmp_path):
    with pytest.raises(RuntimeError, match="not_confirmed"):
        wait_for_parent_gate(tmp_path / "gate", tmp_path / "cancel", timeout_s=0.01)
    (tmp_path / "gate").touch()
    wait_for_parent_gate(tmp_path / "gate", tmp_path / "cancel")
    (tmp_path / "cancel").touch()
    with pytest.raises(RuntimeError, match="cancelled_before_child"):
        wait_for_parent_gate(tmp_path / "gate", tmp_path / "cancel")


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows job ownership")
def test_owned_dummy_child_is_killed_on_job_close():
    job = ChildJob()
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(20)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        job.assign(child)
        assert child.poll() is None
        job.close()
        child.wait(timeout=5)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        job.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows job ownership")
def test_terminate_touches_only_the_assigned_dummy_child():
    job = ChildJob()
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(20)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(20)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        job.assign(child)
        job.terminate()
        assert child.poll() is not None
        assert unrelated.poll() is None
    finally:
        for owned_test_process in (child, unrelated):
            if owned_test_process.poll() is None:
                owned_test_process.kill()
                owned_test_process.wait(timeout=5)
        job.close()

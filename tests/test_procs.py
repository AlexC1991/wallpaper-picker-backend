import os
import signal
import subprocess
import sys
import time

from wallpaper_picker import procs


def test_analyze_log():
    log = """Running with: ./linux-wallpaperengine --screen-root HDMI-A-1 --bg 1
Failed to initialize GLEW, but continuing with EGL context: No GLX display
Fullscreen detection not supported by your Wayland compositor
Cannot load libcuda.so.1
Using hardware decoding (vaapi-copy).
Cannot find shader effects/foo.frag
Unsupported project type application
"""
    errors, warnings = procs.analyze_log(log)
    assert errors == ["Unsupported project type application"]
    assert warnings == ["Cannot find shader effects/foo.frag"]


def test_screen_outputs():
    assert procs.screen_outputs(["lwe", "--screen-root", "DP-1", "--bg", "1", "--screen-span", "A,B"]) == {"DP-1", "A", "B"}
    assert procs.screen_outputs(["lwe", "123"]) == set()


def test_terminate_exact_pid_and_stopped_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        st = procs.starttime(p.pid)
        assert procs.alive(p.pid, st)
        assert not procs.alive(p.pid, st + 1)  # a recycled pid would not match
        os.kill(p.pid, signal.SIGSTOP)
        time.sleep(0.1)
        assert procs.state(p.pid) == "T"
        assert procs.terminate(p.pid, st, timeout=3)
        p.wait(timeout=3)
    finally:
        if p.poll() is None:
            p.kill()


def test_usage_sampler():
    s = procs.UsageSampler()
    u = s.sample(os.getpid())
    assert u.rss_mb > 1 and u.processes >= 1


def test_cef_crash_is_fatal_and_explained():
    errors, _ = procs.analyze_log("Running with: x\nclose symbol missing\n")
    assert errors == ["close symbol missing"]
    assert "web wallpapers" in procs.explain(errors)
    assert procs.explain(["something else"]) is None

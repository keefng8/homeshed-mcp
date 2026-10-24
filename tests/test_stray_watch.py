"""Stray one-off commands (stray_watch.py): only what a `docker exec` started is ever looked at, and only one that
keeps a core busy for the whole stretch is stopped."""
import stray_watch as sw


def proc(root, pid, ppid, ticks=0, cmd="python -", state="S"):
    d = root / str(pid)
    d.mkdir()
    rest = [state, str(ppid)] + ["0"] * 9 + [str(ticks), "0"]  # utime is field 14, stime 15
    (d / "stat").write_text(f"{pid} (py thon) " + " ".join(rest))
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())


def test_only_what_a_docker_exec_started_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "PROC", tmp_path)
    monkeypatch.setattr(sw, "TICKS", 100)
    proc(tmp_path, 1, 0, cmd="python cli.py serve --http")      # the server
    proc(tmp_path, 40, 1, ticks=9000, cmd="git status")          # something the server started
    proc(tmp_path, 50, 0, cmd="sh -c python -c find")            # a docker exec ...
    proc(tmp_path, 51, 50, ticks=4200, cmd="python -c find")     # ... and what it ran
    proc(tmp_path, 60, 1, ticks=100, state="Z")                  # finished: uses nothing
    assert sw.strays() == {50: (0.0, "sh -c python -c find"), 51: (42.0, "python -c find")}


def test_stopped_only_after_the_whole_stretch_busy():
    seen, cpu = {}, 0.0
    for n in range(sw.BUSY_FOR // 60):
        assert sw.step(seen, {7: (cpu, "find /")}, 60) == []
        cpu += 59
    assert sw.step(seen, {7: (cpu, "find /")}, 60) == [(7, "find /")]


def test_a_rest_starts_the_count_again_and_a_gone_one_is_forgotten():
    seen = {}
    sw.step(seen, {7: (0.0, "x")}, 60)
    sw.step(seen, {7: (59.0, "x")}, 60)
    assert seen[7][1] == 60
    sw.step(seen, {7: (60.0, "x")}, 60)  # 1 s in a minute: resting
    assert seen[7][1] == 0
    sw.step(seen, {}, 60)
    assert seen == {}

"""Контракт WinAPI проверяется без Windows; сеть и настоящие Windows-процессы не нужны."""
import ctypes as c
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import youtube_client as client, youtube_download as download, youtube_windows as win
from tests.test_youtube_download import environment, download as import_audio


class Kernel:
    def __init__(self, fail=None):
        self.fail, self.calls, self.handle, self.error = fail, [], 10, 5

    def new(self):
        self.handle += 1
        return self.handle

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1

    def CreateJobObjectW(self, *_):
        self.job = self.new()
        return self.job

    def SetInformationJobObject(self, job, kind, limits, size):
        value = c.cast(limits, c.POINTER(win._ExtendedLimits)).contents
        assert job == self.job and kind == 9 and value.basic.flags == 0x2000
        assert size == c.sizeof(win._ExtendedLimits)
        self.calls.append(("kill_on_close", job))
        return self.fail != "limits"

    def CreatePipe(self, reader, writer, security, _):
        assert c.cast(security, c.POINTER(win._Security)).contents.inherit
        c.cast(reader, c.POINTER(win.HANDLE))[0] = self.new()
        c.cast(writer, c.POINTER(win.HANDLE))[0] = self.new()
        return 1

    def SetHandleInformation(self, handle, mask, flags):
        self.calls.append(("not_inherited", handle.value, mask, flags))
        return 1

    def CreateFileW(self, name, access, share, security, mode, flags, template):
        if name == "NUL":
            assert c.cast(security, c.POINTER(win._Security)).contents.inherit
        else:
            assert flags == 0x200000 and share == 1 and mode == 3
        self.file = self.new()
        return self.file

    def InitializeProcThreadAttributeList(self, attributes, count, flags, size):
        assert count == 2 and flags == 0
        c.cast(size, c.POINTER(win.SIZE))[0] = 128
        if attributes is not None:
            self.calls.append(("initialized",))
            return self.fail != "initialize"
        return 0

    def UpdateProcThreadAttribute(self, attrs, flags, name, values, size, *_):
        handles = tuple(c.cast(values, c.POINTER(win.HANDLE))[index] for index in range(size // c.sizeof(win.HANDLE)))
        self.calls.append(("attribute", name, handles))
        return self.fail != name

    def DeleteProcThreadAttributeList(self, _):
        self.calls.append(("deleted",))

    def CreateProcessW(self, application, command, process_security, thread_security, inherit, flags, env, cwd, startup, process):
        startup = c.cast(startup, c.POINTER(win._StartupEx)).contents
        assert flags == 0x80000 | 0x400 | 0x8000000 and inherit
        assert startup.startup.cb == c.sizeof(win._StartupEx) and startup.startup.flags == 0x100
        attrs = [call for call in self.calls if call[0] == "attribute"]
        assert attrs == [("attribute", 0x20002, (self.file, startup.startup.stdout, startup.startup.stderr)),
                         ("attribute", 0x2000D, (self.job,))]
        self.calls.append(("create", application, command.value, env[:], cwd))
        if self.fail == "create":
            return 0
        process = c.cast(process, c.POINTER(win._Process)).contents
        self.process, self.thread = self.new(), self.new()
        process.process, process.thread = self.process, self.thread
        return 1

    def WaitForSingleObject(self, handle, milliseconds):
        self.calls.append(("wait", handle, milliseconds))
        return 0

    def GetExitCodeProcess(self, _, code):
        c.cast(code, c.POINTER(win.DWORD))[0] = 0
        return 1

    def MoveFileExW(self, source, destination, flags):
        self.calls.append(("move", source, destination, flags))
        return self.fail != "move"

    def GetFileInformationByHandle(self, handle, information):
        information[0] = 0x400 if self.fail == "reparse" else 0
        return 1


def api_for(kernel, monkeypatch):
    api = win.WinAPI(kernel)
    monkeypatch.setattr(api, "last_error", lambda: kernel.error)
    streams = []
    def stream(handle):
        result = io.BytesIO()
        streams.append((handle, result))
        return result
    monkeypatch.setattr(api, "stream", stream)
    return api, streams


def test_process_enters_job_before_running_and_only_standard_handles_are_inherited(monkeypatch):
    kernel = Kernel()
    api, streams = api_for(kernel, monkeypatch)
    arguments = [r"C:\Program Files\tools\yt-dlp.exe", "--", 'ytsearch1:дождь & "forest"']
    process = api.start(arguments, cwd=r"C:\Temp\audio", env={"SystemRoot": r"C:\Windows", "TMP": r"C:\Temp"})
    creation = next(call for call in kernel.calls if call[0] == "create")
    assert creation[1] == arguments[0]
    assert creation[2] == win.subprocess.list2cmdline(arguments)
    assert "SystemRoot=C:\\Windows\x00TMP=C:\\Temp\x00\x00" in creation[3]
    assert len(streams) == 2
    assert process.poll() == 0
    before = len(kernel.calls)
    process.close()
    assert kernel.calls[before:] == [("close", kernel.job), ("wait", kernel.process, 5000), ("close", kernel.process)]
    for _, stream in streams:
        stream.close()


@pytest.mark.parametrize("failure", ["limits", "initialize", 0x20002, 0x2000D, "create"])
def test_failed_isolation_never_runs_uncontained_process_and_closes_handles(monkeypatch, failure):
    kernel = Kernel(fail=failure)
    api, _ = api_for(kernel, monkeypatch)
    with pytest.raises(OSError):
        api.start([r"C:\yt-dlp.exe"], cwd=r"C:\Temp", env={})
    assert ("close", kernel.job) in kernel.calls
    assert not [call for call in kernel.calls if call[0] == "create"] or failure == "create"
    closes = [call[1] for call in kernel.calls if call[0] == "close"]
    assert len(closes) == len(set(closes))
    if failure in {"limits", "initialize"}:
        assert ("deleted",) not in kernel.calls
    else:
        assert ("deleted",) in kernel.calls


class Process:
    def __init__(self, output=b"ok", error=b"", status=0):
        self.stdout, self.stderr = io.BytesIO(output), io.BytesIO(error)
        self.status, self.closed = status, False

    def poll(self):
        return self.status

    def close(self):
        self.closed = True


def run_api(process, monkeypatch):
    observations = []
    def start(argv, **kwargs):
        observations.append((argv, kwargs))
        return process
    api = SimpleNamespace(windows_directory=lambda: r"C:\Windows", start=start)
    monkeypatch.setattr(win, "executable", lambda name: "/tools/" + name + ".exe")
    return api, observations


def test_runner_does_not_inherit_credentials_and_keeps_unicode_arguments(monkeypatch, tmp_path):
    process = Process(output="дождь".encode())
    api, seen = run_api(process, monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "must-not-leak")
    monkeypatch.setenv("SystemRoot", r"C:\attacker")
    result = win.run(["yt-dlp", "--", "дождь & forest"], cwd=str(tmp_path), timeout=1, max_output=100, api=api)
    assert result.decode() == "дождь"
    assert seen[0][0] == ["/tools/yt-dlp.exe", "--", "дождь & forest"]
    env = seen[0][1]["env"]
    assert "SECRET_KEY" not in env and env["SystemRoot"] == r"C:\Windows"
    assert env["TEMP"] == env["TMP"] == str(tmp_path)
    assert process.closed and process.stdout.closed and process.stderr.closed


@pytest.mark.parametrize("stdout", [True, False])
def test_both_streams_have_one_shared_output_limit(monkeypatch, tmp_path, stdout):
    process = Process(output=b"x" * 1000 if stdout else b"", error=b"" if stdout else b"x" * 1000)
    api, _ = run_api(process, monkeypatch)
    with pytest.raises(win.WindowsProcessError, match="output_limit"):
        win.run(["yt-dlp"], cwd=str(tmp_path), timeout=1, max_output=100, api=api)
    assert process.closed


def test_timeout_also_terminates_job_after_pipes_close(monkeypatch, tmp_path):
    process = Process(status=None)
    api, _ = run_api(process, monkeypatch)
    with pytest.raises(win.WindowsProcessError, match="timeout"):
        win.run(["yt-dlp"], cwd=str(tmp_path), timeout=.01, max_output=100, api=api)
    assert process.closed


def test_storage_checks_cancel_process_even_when_it_is_silent(monkeypatch, tmp_path):
    process = Process(status=None)
    api, _ = run_api(process, monkeypatch)
    def full():
        raise download.audio_mirror.LocalDiskFull("local_disk_full")
    with pytest.raises(download.audio_mirror.LocalDiskFull):
        win.run(["yt-dlp"], cwd=str(tmp_path), timeout=1, max_output=100, api=api, check_storage=full)
    assert process.closed


@pytest.mark.parametrize("name,variable", [("yt-dlp", "YOUTUBE_YTDLP_PATH"), ("ffprobe", "YOUTUBE_FFPROBE_PATH")])
def test_admin_can_select_only_absolute_exe_not_shell_or_user_path(monkeypatch, tmp_path, name, variable):
    executable = tmp_path / f"{name}.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setenv(variable, str(executable))
    assert win.executable(name) == str(executable)
    for forbidden in ("evil.exe", str(executable.with_suffix(".cmd")), str(executable.with_suffix(".bat"))):
        monkeypatch.setenv(variable, forbidden)
        with pytest.raises(win.WindowsProcessError, match="not_installed"):
            win.executable(name)


def test_atomic_windows_publication_never_sets_replace_or_copy_flags(monkeypatch):
    kernel = Kernel()
    api, _ = api_for(kernel, monkeypatch)
    api.publish("source", "destination")
    assert kernel.calls == [("move", "source", "destination", 8)]
    kernel.fail, kernel.error = "move", 183
    with pytest.raises(FileExistsError):
        api.publish("source", "destination")


def test_existing_reparse_point_is_rejected_before_any_file_read(monkeypatch):
    kernel = Kernel(fail="reparse")
    api, streams = api_for(kernel, monkeypatch)
    with pytest.raises(OSError, match="youtube_reparse_point"):
        api.open_existing("destination")
    assert streams == [] and ("close", kernel.file) in kernel.calls


def test_client_and_download_delegate_windows_time_and_output_bounds(monkeypatch, tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if kwargs.get("check_storage"):
            kwargs["check_storage"]()
        return b"{}"
    monkeypatch.setattr(win, "run", run)
    monkeypatch.setattr(client, "_WINDOWS", True)
    monkeypatch.setattr(download, "_WINDOWS", True)
    monkeypatch.setattr(download.audio_storage, "free_bytes", lambda _: 1000)
    assert client._run(["yt-dlp", "--", "query"], timeout=2, max_output=123) == b"{}"
    assert download._run(["ffprobe", "audio.m4a"], cwd=str(tmp_path), timeout=3, max_bytes=500, reserve_bytes=10) == "{}"
    assert calls[0][1]["timeout"] == 2 and calls[0][1]["max_output"] == 123
    assert calls[1][0][0] == "ffprobe" and calls[1][1]["timeout"] == 3
    (tmp_path / "audio.part").write_bytes(b"x" * 501)
    with pytest.raises(download.YoutubeDownloadError, match="youtube_too_large"):
        download._run(["yt-dlp"], cwd=str(tmp_path), timeout=3, max_bytes=500, reserve_bytes=10)


def test_windows_download_reuses_matching_file_without_overwriting_and_cleanup_is_owned(environment, monkeypatch):
    db, root = environment
    class Files:
        def publish(self, source, destination):
            if destination.exists():
                raise FileExistsError
            source.rename(destination)
        def open_existing(self, path):
            return path.open("rb")
    monkeypatch.setattr(download, "_WINDOWS", True)
    monkeypatch.setattr(win, "WinAPI", Files)
    first = import_audio(db)
    db.commit()
    second = import_audio(db)
    assert first.audio_file.id == second.audio_file.id
    second.cleanup()
    assert (root / first.audio_file.stored_key).read_bytes() == b"audio"
    first.cleanup()
    assert not (root / first.audio_file.stored_key).exists()


def test_windows_junctions_are_rejected_before_publication(monkeypatch, tmp_path):
    candidate = tmp_path / "junction"
    candidate.mkdir()
    original = Path.lstat
    def lstat(path, *a, **kwargs):
        if path == candidate:
            return SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
        return original(path, *a, **kwargs)
    monkeypatch.setattr(download, "_WINDOWS", True)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(download.YoutubeDownloadError, match="youtube_unsafe_storage_path"):
        download._inside(tmp_path, candidate / "track.m4a")


def test_posix_directory_flush_failure_removes_only_new_publication(monkeypatch, tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"audio")
    monkeypatch.setattr(download, "_WINDOWS", False)
    monkeypatch.setattr(os, "fsync", lambda _: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError):
        download._publish(source, destination)
    assert not destination.exists() and source.read_bytes() == b"audio"

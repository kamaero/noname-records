"""Windows 10+: процесс сразу входит в Job Object, поэтому потомки не переживают отказ."""
from __future__ import annotations

import ctypes as c
import os
from pathlib import Path
import queue
import subprocess
import sys
import sysconfig
import threading
import time

DWORD, WORD, BOOL, HANDLE = c.c_uint32, c.c_uint16, c.c_int32, c.c_void_p
SIZE = c.c_size_t


class _Security(c.Structure):
    _fields_ = [("length", DWORD), ("descriptor", c.c_void_p), ("inherit", BOOL)]


class _Startup(c.Structure):
    _fields_ = [("cb", DWORD), ("reserved", c.c_wchar_p), ("desktop", c.c_wchar_p),
        ("title", c.c_wchar_p), ("x", DWORD), ("y", DWORD), ("width", DWORD),
        ("height", DWORD), ("chars_x", DWORD), ("chars_y", DWORD), ("fill", DWORD),
        ("flags", DWORD), ("show", WORD), ("reserved_size", WORD),
        ("reserved_bytes", c.c_void_p), ("stdin", HANDLE), ("stdout", HANDLE), ("stderr", HANDLE)]


class _StartupEx(c.Structure):
    _fields_ = [("startup", _Startup), ("attributes", c.c_void_p)]


class _Process(c.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class _BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", DWORD),
        ("min_working_set", SIZE), ("max_working_set", SIZE), ("active_processes", DWORD),
        ("affinity", SIZE), ("priority", DWORD), ("scheduling", DWORD)]


class _ExtendedLimits(c.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", c.c_uint64 * 6),
        ("process_memory", SIZE), ("job_memory", SIZE), ("peak_process", SIZE), ("peak_job", SIZE)]


class WindowsProcessError(Exception):
    pass


class WinAPI:
    def __init__(self, kernel=None):
        self.kernel = kernel or c.WinDLL("kernel32", use_last_error=True)
        if kernel is not None:
            return
        signatures = {
            "CreateJobObjectW": (HANDLE, [c.c_void_p, c.c_wchar_p]),
            "SetInformationJobObject": (BOOL, [HANDLE, c.c_int32, c.c_void_p, DWORD]),
            "CreatePipe": (BOOL, [c.POINTER(HANDLE), c.POINTER(HANDLE), c.POINTER(_Security), DWORD]),
            "SetHandleInformation": (BOOL, [HANDLE, DWORD, DWORD]),
            "CreateFileW": (HANDLE, [c.c_wchar_p, DWORD, DWORD, c.c_void_p, DWORD, DWORD, HANDLE]),
            "CloseHandle": (BOOL, [HANDLE]),
            "InitializeProcThreadAttributeList": (BOOL, [c.c_void_p, DWORD, DWORD, c.POINTER(SIZE)]),
            "UpdateProcThreadAttribute": (BOOL, [c.c_void_p, DWORD, SIZE, c.c_void_p, SIZE, c.c_void_p, c.c_void_p]),
            "DeleteProcThreadAttributeList": (None, [c.c_void_p]),
            "CreateProcessW": (BOOL, [c.c_wchar_p, c.c_void_p, c.c_void_p, c.c_void_p, BOOL,
                DWORD, c.c_void_p, c.c_wchar_p, c.POINTER(_StartupEx), c.POINTER(_Process)]),
            "WaitForSingleObject": (DWORD, [HANDLE, DWORD]),
            "GetExitCodeProcess": (BOOL, [HANDLE, c.POINTER(DWORD)]),
            "GetWindowsDirectoryW": (DWORD, [c.c_void_p, DWORD]),
            "MoveFileExW": (BOOL, [c.c_wchar_p, c.c_wchar_p, DWORD]),
            "GetFileInformationByHandle": (BOOL, [HANDLE, c.c_void_p]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, args

    def check(self, success):
        if not success:
            raise OSError(self.last_error(), "windows_operation_failed")
        return success

    def last_error(self):
        return c.get_last_error()

    def close(self, handle):
        if handle:
            self.kernel.CloseHandle(handle)

    def windows_directory(self):
        target = c.create_unicode_buffer(32768)
        size = self.kernel.GetWindowsDirectoryW(target, len(target))
        if not 0 < size < len(target):
            raise OSError("windows_directory_unavailable")
        return target.value

    def stream(self, handle):
        import msvcrt
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.close(handle)
            raise
        try:
            return os.fdopen(descriptor, "rb", buffering=0)
        except BaseException:
            os.close(descriptor)
            raise

    def start(self, argv, *, cwd, env):
        kernel, handles, attributes = self.kernel, [], None
        initialized = False
        process = _Process()
        job = None
        try:
            job = self.check(kernel.CreateJobObjectW(None, None))
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self.check(kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
            security = _Security(c.sizeof(_Security), None, True)
            readers, writers = [], []
            for _ in range(2):
                reader, writer = HANDLE(), HANDLE()
                self.check(kernel.CreatePipe(c.byref(reader), c.byref(writer), c.byref(security), 0))
                handles.extend([reader.value, writer.value])
                self.check(kernel.SetHandleInformation(reader, 1, 0))
                readers.append(reader.value)
                writers.append(writer.value)
            stdin = kernel.CreateFileW("NUL", 0x80000000, 3, c.byref(security), 3, 0, None)
            if stdin in (None, c.c_void_p(-1).value):
                self.check(False)
            handles.append(stdin)
            size = SIZE()
            kernel.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
            if not 0 < size.value <= 65536:
                raise OSError("windows_attributes_unavailable")
            attributes = c.create_string_buffer(size.value)
            self.check(kernel.InitializeProcThreadAttributeList(attributes, 2, 0, c.byref(size)))
            initialized = True
            inherit = (HANDLE * 3)(stdin, *writers)
            jobs = (HANDLE * 1)(job)
            # Явный список не даёт унаследовать открытые файлы других потоков.
            self.check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002, inherit, c.sizeof(inherit), None, None))
            # JOB_LIST присоединяет процесс ДО исполнения первой инструкции.
            self.check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobs, c.sizeof(jobs), None, None))
            startup = _StartupEx()
            startup.startup.cb, startup.startup.flags = c.sizeof(startup), 0x100
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = stdin, *writers
            startup.attributes = c.cast(attributes, c.c_void_p)
            command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")
            self.check(kernel.CreateProcessW(argv[0], command, None, None, True,
                0x80000 | 0x400 | 0x8000000, environment, cwd, c.byref(startup), c.byref(process)))
            self.close(process.thread)
            streams = []
            try:
                for reader in readers:
                    handles.remove(reader)
                    streams.append(self.stream(reader))
            except BaseException:
                for stream in streams:
                    stream.close()
                raise
            result = WindowsProcess(self, process.process, job, streams)
            job, process.process = None, None
            return result
        finally:
            if initialized:
                kernel.DeleteProcThreadAttributeList(attributes)
            # При отказе любого шага Job Object убивает уже созданное дерево.
            self.close(job)
            self.close(process.process)
            for handle in handles:
                self.close(handle)

    def publish(self, source, destination):
        # Без REPLACE_EXISTING и COPY_ALLOWED: только rename на том же томе.
        if not self.kernel.MoveFileExW(str(source), str(destination), 8):
            code = self.last_error()
            if code in {80, 183}:
                raise FileExistsError(str(destination))
            raise OSError(code, "windows_publish_failed")

    def open_existing(self, path):
        # OPEN_REPARSE_POINT открывает саму ссылку; её содержимое не читается.
        handle = self.kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x200000, None)
        if handle in (None, c.c_void_p(-1).value):
            self.check(False)
        try:
            information = (DWORD * 13)()
            self.check(self.kernel.GetFileInformationByHandle(handle, information))
            if information[0] & 0x400:
                raise OSError("youtube_reparse_point")
            transferred = handle
            handle = None
            return self.stream(transferred)
        finally:
            self.close(handle)


class WindowsProcess:
    def __init__(self, api, handle, job, streams):
        self.api, self.handle, self.job = api, handle, job
        self.stdout, self.stderr = streams

    def poll(self):
        status = self.api.kernel.WaitForSingleObject(self.handle, 0)
        if status == 258:
            return None
        if status != 0:
            raise OSError("windows_wait_failed")
        result = DWORD()
        self.api.check(self.api.kernel.GetExitCodeProcess(self.handle, c.byref(result)))
        return result.value

    def close(self):
        try:
            self.api.check(self.api.kernel.CloseHandle(self.job))
            self.job = None
            if self.api.kernel.WaitForSingleObject(self.handle, 5000) != 0:
                raise OSError("windows_cleanup_not_confirmed")
        finally:
            self.api.close(self.handle)
            self.handle = None


def executable(name):
    """PATH пользователя не исполняется; дополнительные бинарники задаёт администратор."""
    variable = "YOUTUBE_YTDLP_PATH" if name == "yt-dlp" else "YOUTUBE_FFPROBE_PATH"
    explicit = os.environ.get(variable)
    if explicit:
        candidate = Path(explicit)
        candidates = [candidate] if candidate.is_absolute() else []
    else:
        candidates = [Path(sysconfig.get_path("scripts")) / (name + ".exe"),
                      Path(sys.executable).parent / (name + ".exe")]
    for candidate in candidates:
        if candidate.is_absolute() and candidate.suffix.lower() == ".exe" and candidate.is_file():
            return str(candidate)
    raise WindowsProcessError("not_installed")


def run(argv, *, cwd, timeout, max_output, check_storage=None, api=None):
    """Потоки читают Windows pipes, очередь и общий счётчик ограничивают память."""
    api = api or WinAPI()
    command = [executable(argv[0]), *argv[1:]]
    windows = api.windows_directory()
    env = {"SystemRoot": windows, "WINDIR": windows, "TEMP": cwd, "TMP": cwd,
           "PATH": str(Path(command[0]).parent), "PYTHONUTF8": "1"}
    process = api.start(command, cwd=cwd, env=env)
    events, stop, threads = queue.Queue(maxsize=4), threading.Event(), []
    output, used, done = [], 0, 0
    deadline = time.monotonic() + timeout

    def send(event):
        while not stop.is_set():
            try:
                events.put(event, timeout=.05)
                return
            except queue.Full:
                pass

    def read(stream, stdout):
        try:
            while not stop.is_set():
                chunk = stream.read(65536)
                if not chunk:
                    break
                send((stdout, chunk))
        except OSError:
            send((None, b"read_failed"))
        finally:
            send((stdout, None))

    try:
        for stream, stdout in ((process.stdout, True), (process.stderr, False)):
            thread = threading.Thread(target=read, args=(stream, stdout), daemon=True)
            threads.append(thread)
            thread.start()
        while done < 2 or process.poll() is None:
            if check_storage:
                check_storage()
            if time.monotonic() >= deadline:
                raise WindowsProcessError("timeout")
            try:
                stdout, chunk = events.get(timeout=min(.05, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if stdout is None:
                raise WindowsProcessError("failed")
            if chunk is None:
                done += 1
                continue
            used += len(chunk)
            if used > max_output:
                raise WindowsProcessError("output_limit")
            if stdout:
                output.append(chunk)
        if check_storage:
            check_storage()
        if process.poll() != 0:
            raise WindowsProcessError("failed")
        return b"".join(output)
    finally:
        stop.set()
        # Закрытие job убивает потомков, которые могли держать pipe после родителя.
        try:
            process.close()
        finally:
            for thread in threads:
                thread.join(timeout=2)
            process.stdout.close()
            process.stderr.close()

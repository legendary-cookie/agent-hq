import os
import signal
import subprocess
import threading

from hq.run import wsl


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.windll.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]


class Child:
    def __init__(self, process, job=None, pidfile=None):
        self.process, self.job, self.pidfile = process, job, pidfile
        self.stdout, self.stderr, self.stdin = process.stdout, process.stderr, process.stdin
        self._kill_lock = threading.Lock()

    def alive(self):
        return self.process.poll() is None

    def kill_tree(self):
        with self._kill_lock:
            if self.pidfile:
                try: wsl.wsl_stop(self.pidfile)
                except (OSError, ValueError): pass
            if os.name == 'nt':
                if self.job: kernel.TerminateJobObject(self.job, 1)
            else:
                try: os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError: return
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired: pass
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def wait(self, timeout=None):
        code = self.process.wait(timeout=timeout)
        return code

    def close(self):
        if self.stdout: self.stdout.close()
        if self.stderr: self.stderr.close()
        if self.stdin and not self.stdin.closed: self.stdin.close()
        with self._kill_lock:
            if os.name == 'nt' and self.job:
                kernel.CloseHandle(self.job)
                self.job = None


def spawn(launch, should_stop=lambda: False):
    kwargs = dict(args=launch.argv, shell=False, cwd=launch.cwd, env=launch.env,
                  stdin=subprocess.PIPE if launch.stdin_text is not None else subprocess.DEVNULL,
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                  encoding='utf-8', errors='replace', bufsize=1)
    if os.name == 'nt':
        kwargs['creationflags'] = 0x4 | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True
    process = subprocess.Popen(**kwargs)
    job = None
    try:
        if should_stop():
            if os.name == 'nt':
                process.kill()
            else:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            return Child(process, None, launch.wsl_pidfile)
        if os.name == 'nt':
            job = kernel.CreateJobObjectW(None, None)
            if not job: raise ctypes.WinError()
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError()
            if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError()
            if should_stop():
                kernel.TerminateJobObject(job, 1)
                return Child(process, job, launch.wsl_pidfile)
            if ctypes.windll.ntdll.NtResumeProcess(wintypes.HANDLE(int(process._handle))) != 0:
                raise OSError('Unable to resume child')
        return Child(process, job, launch.wsl_pidfile)
    except BaseException:
        process.kill()
        process.wait()
        if job: kernel.CloseHandle(job)
        if process.stdout: process.stdout.close()
        if process.stderr: process.stderr.close()
        if process.stdin: process.stdin.close()
        raise

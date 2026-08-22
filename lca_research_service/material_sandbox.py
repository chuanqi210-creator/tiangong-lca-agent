"""Versioned, fail-closed OS boundaries for untrusted material parsing."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import socket
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .container_source_lock import verify_container_source_lock


ADAPTER_CONTRACT = "material_sandbox_adapter/v1"
MATERIAL_MEMORY_BYTES = 768 * 1024 * 1024
_SECCOMP_VERSION = (2, 6, 0)
_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000 | errno.EPERM
_SCMP_SYS_ERROR = -1
_AARCH64_PSEUDO_FORK_SYSCALLS = frozenset({"fork", "vfork"})
_PROC_PIDTASKINFO = 4
_LOCK_PATH = Path(__file__).resolve().parents[1] / "deploy/container-source-lock.json"


class _SeccompVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint),
        ("minor", ctypes.c_uint),
        ("micro", ctypes.c_uint),
    ]


class _ProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("context_switches", ctypes.c_int32),
        ("thread_count", ctypes.c_int32),
        ("running_threads", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


class MaterialSandboxAdapter(ABC):
    """One qualified in-process sandbox and its memory enforcement contract."""

    contract = ADAPTER_CONTRACT
    version = "1"
    adapter_id: str
    memory_monitor: str

    @abstractmethod
    def probe(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def enter(self) -> None:
        raise NotImplementedError

    def apply_memory_limit(self, memory_bytes: int) -> None:
        if (
            not isinstance(memory_bytes, int)
            or isinstance(memory_bytes, bool)
            or memory_bytes <= 0
        ):
            raise ValueError("material memory ceiling is invalid")
        if not self.probe()["memory_ready"]:
            raise RuntimeError("material memory monitor is unavailable")

    @abstractmethod
    def resident_bytes(self, pid: int) -> int:
        raise NotImplementedError


class DarwinMaterialSandboxAdapter(MaterialSandboxAdapter):
    adapter_id = "darwin-sandbox-init"
    memory_monitor = "darwin-libproc-rss"
    _profile = (
        b"(version 1)(allow default)(deny network*)"
        b"(deny process-fork)(deny process-exec)"
    )

    @staticmethod
    def _sandbox_init() -> Any:
        try:
            function = ctypes.CDLL(None).sandbox_init
        except (OSError, AttributeError) as error:
            raise RuntimeError("Darwin sandbox_init is unavailable") from error
        function.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_char_p),
        ]
        function.restype = ctypes.c_int
        return function

    @staticmethod
    def _proc_pidinfo() -> Any:
        try:
            function = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidinfo
        except (OSError, AttributeError) as error:
            raise RuntimeError("Darwin process-memory monitor is unavailable") from error
        function.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int
        ]
        function.restype = ctypes.c_int
        return function

    def probe(self) -> dict[str, Any]:
        sandbox_ready = memory_ready = False
        try:
            self._sandbox_init()
            sandbox_ready = True
        except (OSError, RuntimeError):
            pass
        try:
            self.resident_bytes(os.getpid())
            memory_ready = True
        except (OSError, RuntimeError):
            pass
        return _probe(self, sandbox_ready=sandbox_ready, memory_ready=memory_ready)

    def enter(self) -> None:
        error_buffer = ctypes.c_char_p()
        if self._sandbox_init()(self._profile, 0, ctypes.byref(error_buffer)) != 0:
            raise RuntimeError("Darwin material sandbox initialization failed")

    def resident_bytes(self, pid: int) -> int:
        info = _ProcTaskInfo()
        size = ctypes.sizeof(info)
        if (
            self._proc_pidinfo()(pid, _PROC_PIDTASKINFO, 0, ctypes.byref(info), size)
            != size
        ):
            raise RuntimeError("Darwin process-memory monitor failed")
        return int(info.resident_size)


class LinuxMaterialSandboxAdapter(MaterialSandboxAdapter):
    adapter_id = "linux-libseccomp"
    memory_monitor = "linux-rlimit-as-proc-rss"
    activation_probe_timeout_seconds = 5.0
    denied_syscalls = (
        "socket",
        "socketpair",
        "connect",
        "fork",
        "vfork",
        "clone",
        "clone3",
        "execve",
        "execveat",
        "ptrace",
    )

    @staticmethod
    def _library() -> Any:
        try:
            architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
                os.uname().machine
            )
            if architecture is None:
                raise RuntimeError("unsupported container architecture")
            lock = verify_container_source_lock(
                repository_root=_LOCK_PATH.parents[1],
                lock_path=_LOCK_PATH,
                architecture=architecture,
            )
            package = lock["system_packages"]["libseccomp2"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError, RuntimeError) as error:
            raise RuntimeError("Linux sandbox source lock is unavailable") from error
        if (
            package.get("version") != "2.6.0-2"
            or package.get("library_soname") != "libseccomp.so.2"
            or package.get("runtime_version") != ".".join(map(str, _SECCOMP_VERSION))
        ):
            raise RuntimeError("Linux sandbox source lock is not qualified")
        try:
            library = ctypes.CDLL(package["library_soname"])
            library.seccomp_version.restype = ctypes.POINTER(_SeccompVersion)
            version_pointer = library.seccomp_version()
            if not version_pointer:
                raise RuntimeError("Linux libseccomp version is unavailable")
            version = version_pointer.contents
            observed = (version.major, version.minor, version.micro)
            if observed != _SECCOMP_VERSION:
                raise RuntimeError("Linux libseccomp version is not qualified")
            library.seccomp_init.argtypes = [ctypes.c_uint32]
            library.seccomp_init.restype = ctypes.c_void_p
            library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
            library.seccomp_syscall_resolve_name.restype = ctypes.c_int
            library.seccomp_rule_add.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint
            ]
            library.seccomp_rule_add.restype = ctypes.c_int
            library.seccomp_load.argtypes = [ctypes.c_void_p]
            library.seccomp_load.restype = ctypes.c_int
            library.seccomp_release.argtypes = [ctypes.c_void_p]
            library.seccomp_release.restype = None
        except RuntimeError:
            raise
        except (
            OSError,
            AttributeError,
            TypeError,
            ValueError,
            ctypes.ArgumentError,
        ) as error:
            raise RuntimeError("qualified Linux libseccomp ABI is unavailable") from error
        return library

    def _install_filter(
        self, library: Any, *, native_architecture: str | None = None
    ) -> None:
        architecture = native_architecture or os.uname().machine
        try:
            context = library.seccomp_init(_SCMP_ACT_ALLOW)
        except (AttributeError, TypeError, ValueError, ctypes.ArgumentError) as error:
            raise RuntimeError("Linux material sandbox initialization failed") from error
        if not context:
            raise RuntimeError("Linux material sandbox initialization failed")
        try:
            resolved: dict[str, int] = {}
            for name in self.denied_syscalls:
                try:
                    syscall = library.seccomp_syscall_resolve_name(
                        name.encode("ascii")
                    )
                except (
                    AttributeError,
                    TypeError,
                    ValueError,
                    ctypes.ArgumentError,
                ) as error:
                    raise RuntimeError(
                        "Linux material sandbox rule is unavailable"
                    ) from error
                is_aarch64_fork_pseudo = (
                    architecture == "aarch64"
                    and name in _AARCH64_PSEUDO_FORK_SYSCALLS
                    and syscall < _SCMP_SYS_ERROR
                )
                if syscall == _SCMP_SYS_ERROR or (
                    syscall < 0 and not is_aarch64_fork_pseudo
                ):
                    raise RuntimeError("Linux material sandbox rule is unavailable")
                resolved[name] = syscall

            skipped = {
                name
                for name, syscall in resolved.items()
                if architecture == "aarch64"
                and name in _AARCH64_PSEUDO_FORK_SYSCALLS
                and syscall < _SCMP_SYS_ERROR
            }
            added: set[str] = set()
            rule_order = ("clone", "clone3") + tuple(
                name
                for name in self.denied_syscalls
                if name not in {"clone", "clone3"}
            )
            for name in rule_order:
                if name in skipped:
                    continue
                try:
                    rule_result = library.seccomp_rule_add(
                        context, _SCMP_ACT_ERRNO, resolved[name], 0
                    )
                except (
                    AttributeError,
                    TypeError,
                    ValueError,
                    ctypes.ArgumentError,
                ) as error:
                    raise RuntimeError(
                        "Linux material sandbox rule is unavailable"
                    ) from error
                if rule_result != 0:
                    raise RuntimeError("Linux material sandbox rule is unavailable")
                added.add(name)
            if skipped and not {"clone", "clone3"}.issubset(added):
                raise RuntimeError("Linux material sandbox rule is unavailable")
            try:
                load_result = library.seccomp_load(context)
            except (
                AttributeError,
                TypeError,
                ValueError,
                ctypes.ArgumentError,
            ) as error:
                raise RuntimeError("Linux material sandbox activation failed") from error
            if load_result != 0:
                raise RuntimeError("Linux material sandbox activation failed")
        finally:
            try:
                library.seccomp_release(context)
            except (AttributeError, TypeError, ValueError, ctypes.ArgumentError) as error:
                raise RuntimeError("Linux material sandbox release failed") from error

    @staticmethod
    def _operation_is_denied(operation: Any) -> bool:
        try:
            result = operation()
        except OSError as error:
            return error.errno == errno.EPERM
        if isinstance(result, tuple):
            for item in result:
                if hasattr(item, "close"):
                    item.close()
        elif hasattr(result, "close"):
            result.close()
        return False

    @staticmethod
    def _fork_is_denied() -> bool:
        try:
            pid = os.fork()
        except OSError as error:
            return error.errno == errno.EPERM
        if pid == 0:
            os._exit(91)
        os.waitpid(pid, 0)
        return False

    @staticmethod
    def _ptrace_is_denied() -> bool:
        try:
            library = ctypes.CDLL(None, use_errno=True)
            function = library.ptrace
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            function.restype = ctypes.c_long
            ctypes.set_errno(0)
            result = function(-1, 0, None, None)
            return result == -1 and ctypes.get_errno() == errno.EPERM
        except (
            OSError,
            AttributeError,
            TypeError,
            ValueError,
            ctypes.ArgumentError,
        ):
            return False

    def _activation_self_test(self, *, expected_euid: int | None = None) -> bool:
        """Load the real filter and test it only inside a sacrificial process."""

        if expected_euid is not None and os.geteuid() != expected_euid:
            return False
        try:
            connect_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return False
        try:
            self._install_filter(self._library())
            checks = (
                lambda: self._connect_is_denied(connect_socket),
                lambda: self._operation_is_denied(
                    lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                ),
                lambda: self._operation_is_denied(socket.socketpair),
                self._fork_is_denied,
                self._ptrace_is_denied,
                lambda: self._operation_is_denied(
                    lambda: os.execv(
                        "/bin/sh", ["/bin/sh", "-c", "exit 92"]
                    )
                ),
                lambda: self._operation_is_denied(
                    lambda: os.execv(
                        sys.executable,
                        [sys.executable, "-c", "raise SystemExit(93)"],
                    )
                ),
            )
            return all(check() for check in checks)
        except (OSError, RuntimeError):
            return False
        finally:
            connect_socket.close()

    @staticmethod
    def _connect_is_denied(connect_socket: socket.socket) -> bool:
        try:
            connect_socket.connect(("127.0.0.1", 9))
        except OSError as error:
            return error.errno == errno.EPERM
        return False

    def activation_probe_command(self) -> list[str]:
        return [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning",
            "-m",
            "lca_research_service.material_sandbox",
            "--linux-activation-probe-child",
            str(os.geteuid()),
        ]

    def _activation_probe_passed(self) -> bool:
        try:
            completed = subprocess.run(
                self.activation_probe_command(),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={"PYTHONNOUSERSITE": "1"},
                timeout=self.activation_probe_timeout_seconds,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def probe(self) -> dict[str, Any]:
        sandbox_ready = memory_ready = False
        sandbox_ready = self._activation_probe_passed()
        try:
            self.resident_bytes(os.getpid())
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            memory_ready = hard == resource.RLIM_INFINITY or hard > 0
        except (OSError, RuntimeError, ValueError):
            pass
        return _probe(self, sandbox_ready=sandbox_ready, memory_ready=memory_ready)

    def enter(self) -> None:
        self._install_filter(self._library())

    def apply_memory_limit(self, memory_bytes: int) -> None:
        if (
            not isinstance(memory_bytes, int)
            or isinstance(memory_bytes, bool)
            or memory_bytes <= 0
        ):
            raise ValueError("material memory ceiling is invalid")
        self.resident_bytes(os.getpid())
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        bounded = (
            memory_bytes
            if hard == resource.RLIM_INFINITY
            else min(memory_bytes, hard)
        )
        resource.setrlimit(resource.RLIMIT_AS, (bounded, bounded))

    def resident_bytes(self, pid: int) -> int:
        try:
            with Path(f"/proc/{pid}/status").open(encoding="ascii") as status:
                for line in status:
                    if line.startswith("VmRSS:"):
                        fields = line.split()
                        if len(fields) == 3 and fields[2] == "kB":
                            return int(fields[1]) * 1024
        except (OSError, ValueError) as error:
            raise RuntimeError("Linux process-memory monitor failed") from error
        raise RuntimeError("Linux process-memory monitor failed")


def _probe(
    adapter: MaterialSandboxAdapter, *, sandbox_ready: bool, memory_ready: bool
) -> dict[str, Any]:
    return {
        "adapter_contract": adapter.contract,
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.version,
        "memory_monitor": adapter.memory_monitor,
        "sandbox_ready": sandbox_ready,
        "memory_ready": memory_ready,
        "runtime_euid": os.geteuid(),
        "ready": sandbox_ready and memory_ready,
    }


def material_sandbox_adapter() -> MaterialSandboxAdapter:
    if sys.platform == "darwin":
        return DarwinMaterialSandboxAdapter()
    if sys.platform.startswith("linux"):
        return LinuxMaterialSandboxAdapter()
    raise RuntimeError("material sandbox platform is unsupported")


def probe_material_runtime() -> dict[str, Any]:
    try:
        return material_sandbox_adapter().probe()
    except Exception:
        return {
            "adapter_contract": ADAPTER_CONTRACT,
            "adapter_id": "unsupported",
            "adapter_version": "1",
            "memory_monitor": "unsupported",
            "sandbox_ready": False,
            "memory_ready": False,
            "runtime_euid": os.geteuid(),
            "ready": False,
        }


def enter_material_sandbox() -> None:
    adapter = material_sandbox_adapter()
    if not adapter.probe()["ready"]:
        raise RuntimeError("material sandbox runtime is unavailable")
    adapter.enter()


def apply_material_memory_limit(memory_bytes: int = MATERIAL_MEMORY_BYTES) -> None:
    material_sandbox_adapter().apply_memory_limit(memory_bytes)


def material_resident_bytes(pid: int) -> int:
    return material_sandbox_adapter().resident_bytes(pid)


def _self_test() -> int:
    return 0 if probe_material_runtime()["ready"] else 2


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--linux-activation-probe-child":
        try:
            expected_uid = int(sys.argv[2])
        except ValueError:
            raise SystemExit(2) from None
        raise SystemExit(
            0
            if LinuxMaterialSandboxAdapter()._activation_self_test(
                expected_euid=expected_uid
            )
            else 1
        )
    raise SystemExit(_self_test())

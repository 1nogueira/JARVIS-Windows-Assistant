from __future__ import annotations

import ctypes
import glob
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable


STOP_WORDS = {
    "a", "o", "as", "os", "de", "do", "da", "dos", "das", "um", "uma",
    "meu", "minha", "app", "aplicativo", "application", "programa", "por", "favor",
}
ALIASES = {
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "code": "visual studio code",
    "epic": "epic games launcher",
    "epic games": "epic games launcher",
    "calculadora": "calculator",
    "bloco de notas": "notepad",
    "camera": "camera",
    "camera do windows": "camera",
}
PROTOCOLS = {
    "discord": ("Discord", "discord://", ("discord.exe",)),
    "whatsapp": ("WhatsApp", "whatsapp:", ("whatsapp.exe",)),
}
KNOWN_PATHS = {
    "visual studio code": (
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
    ),
    "epic games launcher": (
        r"%PROGRAMFILES%\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
        r"%PROGRAMFILES(X86)%\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
    ),
    "discord": (
        r"%LOCALAPPDATA%\Discord\app-*\Discord.exe",
        r"%LOCALAPPDATA%\Programs\Discord\Discord.exe",
    ),
}
SOURCE_PRIORITY = {
    "known_path": 7,
    "app_paths": 6,
    "protocol": 5,
    "start_app": 4,
    "shortcut": 3,
    "path": 2,
}


def normalize_app_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
    text = re.sub(r"^(?:jarvis\s+)?(?:abra|abrir|abre|inicie|iniciar|execute|executar)\s+", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token not in STOP_WORDS]
    normalized = " ".join(tokens).strip()
    return ALIASES.get(normalized, normalized)


@dataclass(frozen=True, slots=True)
class AppCandidate:
    name: str
    launch_type: str
    target: str
    source: str
    process_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["process_names"] = list(self.process_names)
        value["aliases"] = list(self.aliases)
        return value


@dataclass(frozen=True, slots=True)
class AppResolution:
    status: str
    query: str
    candidate: AppCandidate | None = None
    candidates: tuple[AppCandidate, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    handle: int
    pid: int
    title: str
    class_name: str


@dataclass(slots=True)
class _Index:
    candidates: list[AppCandidate] = field(default_factory=list)
    created_at: float = 0.0


class WindowsAppResolver:
    """Cached resolver for Win32, Start Menu, UWP and registered applications."""

    def __init__(self, *, cache_ttl_seconds: float = 600.0) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._index = _Index()
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._index = _Index()

    def warm(self) -> int:
        """Build the shared index once without launching an application."""
        return len(self._load_index())

    def discover(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        normalized = normalize_app_name(query)
        ranked = self._rank(normalized)
        return [
            {**candidate.public_dict(), "score": round(score, 4)}
            for score, candidate in ranked[: max(1, limit)]
            if score >= 0.58
        ]

    def resolve(self, query: str) -> AppResolution:
        normalized = normalize_app_name(query)
        if not normalized:
            return AppResolution("not_found", query, reason="Nome de aplicativo vazio.")

        protocol = PROTOCOLS.get(normalized)
        if protocol:
            name, uri, process_names = protocol
            return AppResolution(
                "found",
                query,
                candidate=AppCandidate(name, "protocol", uri, "protocol", process_names),
            )

        known = self._known_path_candidate(normalized)
        if known:
            return AppResolution("found", query, candidate=known)

        ranked = self._rank(normalized)
        credible = [(score, item) for score, item in ranked if score >= 0.72]
        if not credible:
            return AppResolution("not_found", query, reason=f"Aplicativo não encontrado: {query}")

        best_score, best = credible[0]
        # A generic launcher request must name one unique credible application.
        if normalized == "launcher":
            launcher_matches = [
                item for score, item in credible
                if score >= 0.72 and "launcher" in normalize_app_name(item.name).split()
            ]
            unique = self._deduplicate_names(launcher_matches)
            if len(unique) != 1:
                return AppResolution(
                    "ambiguous",
                    query,
                    candidates=tuple(unique[:8]),
                    reason="Há mais de um launcher instalado; escolha um nome específico.",
                )
            return AppResolution("found", query, candidate=unique[0])

        close = [
            item for score, item in credible
            if score >= max(0.78, best_score - 0.025) and self._identity(item) != self._identity(best)
        ]
        if close and best_score < 0.96:
            options = self._deduplicate_names([best, *close])
            return AppResolution(
                "ambiguous",
                query,
                candidates=tuple(options[:8]),
                reason="Encontrei mais de um aplicativo compatível.",
            )
        return AppResolution("found", query, candidate=best)

    def launch(
        self,
        query: str,
        *,
        startfile: Callable[[str], Any] | None = None,
        verification_timeout: float = 5.0,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        resolution = self.resolve(query)
        if resolution.status != "found" or not resolution.candidate:
            return {
                "success": False,
                "verified": False,
                "requested": query,
                "error": "ambiguous_application" if resolution.status == "ambiguous" else "application_not_found",
                "detail": resolution.reason,
                "candidates": [item.public_dict() for item in resolution.candidates],
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }

        candidate = resolution.candidate
        before_processes = _process_snapshot()
        before_windows = _window_snapshot()
        launched_pid: int | None = None
        try:
            if candidate.launch_type == "exe":
                process = subprocess.Popen(
                    [candidate.target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                launched_pid = process.pid
            elif candidate.launch_type in {"protocol", "shortcut"}:
                (startfile or os.startfile)(candidate.target)
            elif candidate.launch_type == "start_app":
                process = subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{candidate.target}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                launched_pid = process.pid
            else:
                raise RuntimeError(f"Tipo de inicialização não suportado: {candidate.launch_type}")
        except OSError as exc:
            return {
                "success": False,
                "verified": False,
                "requested": query,
                "resolved_name": candidate.name,
                "launch_type": candidate.launch_type,
                "target": candidate.target,
                "error": "launch_failed",
                "detail": str(exc),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }

        evidence = _wait_for_launch_evidence(
            candidate,
            before_processes,
            before_windows,
            launched_pid,
            timeout=verification_timeout,
        )
        verified = bool(evidence)
        return {
            "success": verified,
            "verified": verified,
            "opened": candidate.name if verified else None,
            "requested": query,
            "resolved_name": candidate.name,
            "launch_type": candidate.launch_type,
            "target": candidate.target,
            "source": candidate.source,
            "pid": launched_pid,
            "verification": evidence or "no_process_or_window_evidence",
            "error": None if verified else "launch_not_verified",
            "detail": (
                "Aplicativo aberto e verificado."
                if verified
                else "O Windows recebeu o comando, mas nenhum processo ou janela compatível foi confirmado."
            ),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def _rank(self, normalized: str) -> list[tuple[float, AppCandidate]]:
        ranked = [
            (self._score(normalized, item), item)
            for item in self._load_index()
        ]
        ranked = [(score, item) for score, item in ranked if score > 0]
        ranked.sort(
            key=lambda pair: (pair[0], SOURCE_PRIORITY.get(pair[1].source, 0)),
            reverse=True,
        )
        return ranked

    def _score(self, query: str, candidate: AppCandidate) -> float:
        names = {
            normalize_app_name(candidate.name),
            normalize_app_name(Path(candidate.target).stem),
            *(normalize_app_name(alias) for alias in candidate.aliases),
        }
        names.discard("")
        if query in names:
            return 1.0
        query_tokens = set(query.split())
        best = 0.0
        for name in names:
            name_tokens = set(name.split())
            overlap = query_tokens & name_tokens
            if not overlap:
                continue
            if query_tokens <= name_tokens:
                best = max(best, 0.93 if len(query_tokens) > 1 else 0.82)
            elif name_tokens <= query_tokens:
                best = max(best, 0.9)
            else:
                coverage = len(overlap) / max(len(query_tokens), len(name_tokens))
                best = max(best, 0.7 + coverage * 0.18)
            if len(query_tokens) > 1 and len(name_tokens) > 1:
                best = max(best, SequenceMatcher(None, query, name).ratio() * 0.86)
        return best

    def _load_index(self) -> list[AppCandidate]:
        now = time.monotonic()
        if self._index.candidates and now - self._index.created_at < self.cache_ttl_seconds:
            return list(self._index.candidates)
        with self._lock:
            now = time.monotonic()
            if self._index.candidates and now - self._index.created_at < self.cache_ttl_seconds:
                return list(self._index.candidates)
            candidates: list[AppCandidate] = []
            candidates.extend(self._known_candidates())
            candidates.extend(self._registry_candidates())
            candidates.extend(self._start_app_candidates())
            candidates.extend(self._shortcut_candidates())
            candidates.extend(self._path_candidates())
            self._index = _Index(self._deduplicate(candidates), time.monotonic())
            return list(self._index.candidates)

    def _known_path_candidate(self, normalized: str) -> AppCandidate | None:
        for candidate in self._known_candidates():
            if normalize_app_name(candidate.name) == normalized:
                return candidate
        return None

    def _known_candidates(self) -> list[AppCandidate]:
        found: list[AppCandidate] = []
        for name, patterns in KNOWN_PATHS.items():
            for pattern in patterns:
                expanded = os.path.expandvars(pattern)
                paths = [Path(item) for item in glob.glob(expanded)] if "*" in expanded else [Path(expanded)]
                paths = sorted((path for path in paths if path.is_file()), reverse=True)
                if paths:
                    path = paths[0]
                    found.append(
                        AppCandidate(
                            name.title() if name != "visual studio code" else "Visual Studio Code",
                            "exe",
                            str(path),
                            "known_path",
                            (path.name.casefold(),),
                            tuple(alias for alias, canonical in ALIASES.items() if canonical == name),
                        )
                    )
                    break
        return found

    def _registry_candidates(self) -> list[AppCandidate]:
        if os.name != "nt":
            return []
        try:
            import winreg
        except ImportError:
            return []
        candidates: list[AppCandidate] = []
        roots = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        )
        for hive, path in roots:
            try:
                with winreg.OpenKey(hive, path) as root:
                    count = winreg.QueryInfoKey(root)[0]
                    for index in range(count):
                        key_name = winreg.EnumKey(root, index)
                        try:
                            with winreg.OpenKey(root, key_name) as key:
                                target = str(winreg.QueryValueEx(key, None)[0]).strip('"')
                        except OSError:
                            continue
                        executable = Path(target)
                        if executable.is_file():
                            candidates.append(
                                AppCandidate(
                                    executable.stem,
                                    "exe",
                                    str(executable),
                                    "app_paths",
                                    (executable.name.casefold(),),
                                )
                            )
            except OSError:
                continue
        return candidates

    def _start_app_candidates(self) -> list[AppCandidate]:
        if os.name != "nt":
            return []
        script = "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode or not completed.stdout.strip():
            return []
        try:
            raw: Any = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return []
        values = raw if isinstance(raw, list) else [raw]
        return [
            AppCandidate(
                str(item["Name"]),
                "start_app",
                str(item["AppID"]),
                "start_app",
                _process_names_from_app(str(item["Name"]), str(item["AppID"])),
            )
            for item in values
            if isinstance(item, dict) and item.get("Name") and item.get("AppID")
        ]

    def _shortcut_candidates(self) -> list[AppCandidate]:
        locations = [
            Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")),
            Path(os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs")),
        ]
        result: list[AppCandidate] = []
        for location in locations:
            if not location.is_dir():
                continue
            for shortcut in location.rglob("*.lnk"):
                result.append(
                    AppCandidate(shortcut.stem, "shortcut", str(shortcut), "shortcut")
                )
        return result

    def _path_candidates(self) -> list[AppCandidate]:
        result: list[AppCandidate] = []
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            path = Path(directory.strip('"'))
            if not path.is_dir():
                continue
            try:
                executables = path.glob("*.exe")
                for executable in executables:
                    result.append(
                        AppCandidate(
                            executable.stem,
                            "exe",
                            str(executable),
                            "path",
                            (executable.name.casefold(),),
                        )
                    )
            except OSError:
                continue
        return result

    @staticmethod
    def _identity(candidate: AppCandidate) -> tuple[str, str]:
        return candidate.launch_type, os.path.normcase(candidate.target)

    def _deduplicate(self, candidates: list[AppCandidate]) -> list[AppCandidate]:
        unique: dict[tuple[str, str], AppCandidate] = {}
        for item in candidates:
            key = self._identity(item)
            current = unique.get(key)
            if current is None or SOURCE_PRIORITY.get(item.source, 0) > SOURCE_PRIORITY.get(current.source, 0):
                unique[key] = item
        return list(unique.values())

    def _deduplicate_names(self, candidates: list[AppCandidate]) -> list[AppCandidate]:
        unique: dict[str, AppCandidate] = {}
        for item in candidates:
            key = normalize_app_name(item.name)
            current = unique.get(key)
            if current is None or SOURCE_PRIORITY.get(item.source, 0) > SOURCE_PRIORITY.get(current.source, 0):
                unique[key] = item
        return list(unique.values())


def _process_names_from_app(name: str, app_id: str) -> tuple[str, ...]:
    known = {
        "camera": ("windowscamera.exe", "camera.exe"),
        "visual studio code": ("code.exe",),
        "epic games launcher": ("epicgameslauncher.exe",),
        "discord": ("discord.exe",),
        "whatsapp": ("whatsapp.exe",),
    }
    normalized = normalize_app_name(name)
    if normalized in known:
        return known[normalized]
    if app_id.casefold().endswith(".exe"):
        return (Path(app_id).name.casefold(),)
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return (f"{compact}.exe",) if compact else ()


def _process_snapshot() -> dict[int, tuple[str, str]]:
    try:
        import psutil
    except ImportError:
        return {}
    result: dict[int, tuple[str, str]] = {}
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            result[int(process.info["pid"])] = (
                str(process.info.get("name") or "").casefold(),
                str(process.info.get("exe") or "").casefold(),
            )
        except (psutil.Error, TypeError, ValueError):
            continue
    return result


def _window_snapshot() -> dict[int, WindowSnapshot]:
    if os.name != "nt":
        return {}
    user32 = ctypes.windll.user32
    windows: dict[int, WindowSnapshot] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def collect(handle: int, _: int) -> bool:
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, title_buffer, length + 1)
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, class_buffer, 256)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        windows[int(handle)] = WindowSnapshot(
            int(handle), int(pid.value), title_buffer.value, class_buffer.value
        )
        return True

    user32.EnumWindows(collect, 0)
    return windows


def _wait_for_launch_evidence(
    candidate: AppCandidate,
    before_processes: dict[int, tuple[str, str]],
    before_windows: dict[int, WindowSnapshot],
    launched_pid: int | None,
    *,
    timeout: float,
) -> str:
    expected_names = {name.casefold() for name in candidate.process_names}
    if candidate.launch_type == "exe":
        expected_names.add(Path(candidate.target).name.casefold())
    title_tokens = {
        token for token in normalize_app_name(candidate.name).split()
        if len(token) >= 3 and token not in {"visual", "studio", "games", "launcher"}
    }
    stable_process_after = time.monotonic() + 0.35
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        now = time.monotonic()
        processes = _process_snapshot()
        windows = _window_snapshot()
        if (
            candidate.launch_type == "exe"
            and launched_pid
            and launched_pid in processes
            and launched_pid not in before_processes
            and now >= stable_process_after
        ):
            return f"new_process_pid:{launched_pid}"
        for pid, (name, executable) in processes.items():
            if expected_names and (name in expected_names or any(executable.endswith("\\" + expected) for expected in expected_names)):
                if pid not in before_processes and now >= stable_process_after:
                    return f"new_process:{name}:{pid}"
                if any(window.pid == pid for window in windows.values()):
                    return f"visible_window_process:{name}:{pid}"
        for handle, window in windows.items():
            normalized_title = normalize_app_name(window.title)
            if title_tokens and title_tokens & set(normalized_title.split()):
                prefix = "new_window" if handle not in before_windows else "visible_window"
                return f"{prefix}:{handle}:{window.title}"
        time.sleep(0.2)
    return ""


_resolver: WindowsAppResolver | None = None
_resolver_lock = threading.Lock()


def get_app_resolver() -> WindowsAppResolver:
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = WindowsAppResolver()
    return _resolver

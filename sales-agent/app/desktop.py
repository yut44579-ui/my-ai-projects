"""desktop.py · 把导出文件**直接落到本机桌面** + 在资源管理器里定位它（FR-002B）。

════════════════════════════════════════════════════════════════════════
【这是**本机部署特例**，不是通用 Web 下载机制 —— 先读这一段再改代码】
════════════════════════════════════════════════════════════════════════
"服务端把文件写到用户桌面"这件事**只在下面这个前提下成立**：

    服务端进程与用户桌面**同一台机器、同一个 Windows 会话**（用户本人 127.0.0.1 访问）。

本项目当前就是这种情况（本机单用户自用），所以成立。**一旦哪天部署到别的机器上**
（容器 / 服务器 / 多用户），这个模块的 `save_to_desktop` 会写到**那台机器的**某个桌面、
或者在有多个交互用户时**根本解析不出**该写谁的桌面 —— 那时这条能力必须停用或换成
真正的"浏览器下载 + 用户自选位置"。不要把本模块抽象成"通用的服务端下载方案"。

用户报的原话是「虽然能够生成 word / excel / markdown，但还是那个问题，**无法打开**。
你帮我弄到桌面」—— 真因不是文件坏了，而是**文件掉在了别处**（用户 Edge 配置的下载目录
被设成了 `D:\\`），所以这里要解决的是"文件去哪儿"，不是"文件能不能打开"。

════════════════════════════════════════════════════════════════════════
【桌面路径怎么来的：系统机制，不是拼字符串】
════════════════════════════════════════════════════════════════════════
按顺序问系统（`resolve_desktop_dir`）：

    ① `SHGetKnownFolderPath(FOLDERID_Desktop)` —— Windows 外壳 API，权威答案。
       OneDrive 把桌面重定向到 `…\\OneDrive\\Desktop`、用户手工改过桌面位置，它都如实返回。
    ② 注册表 `HKCU\\…\\Explorer\\User Shell Folders` 的 `Desktop`（REG_EXPAND_SZ，
       值里可能是 `%USERPROFILE%\\OneDrive\\Desktop`，要展开环境变量）。
    ③ `SHGetFolderPathW(CSIDL_DESKTOPDIRECTORY)` —— 老 API，前两个都不可用时的兜底。

三个都是**问系统要答案**。**明确不用** `Path.home() / "Desktop"`：中文 Windows、
OneDrive 重定向、组策略改桌面位置——只要不是"默认英文、没重定向"，它就是错的。
也**不硬编码** `C:\\Users\\<某个人>\\Desktop`。

拿到候选目录后**必须 `is_dir()` 验证**；三个都没给出可用目录 → 抛
`DesktopError("desktop_unavailable")`，**明确报错**，绝不静默写到别的地方（宁可不落盘）。

`SRA_DESKTOP_DIR` 是一个**显式的运维/测试覆盖**（不是兜底）：设了就用它，但它同样要过
`is_dir()` 验证，不是目录一样报错。

════════════════════════════════════════════════════════════════════════
【落盘的两条硬要求】
════════════════════════════════════════════════════════════════════════
· **原子写**：先在**同一个目录**写临时文件 → `os.replace` 改名成最终文件（同卷 rename 是
  原子的，用户不会看到半截文件）。中途任何失败都把临时文件和占位文件清掉，不留残渣。
· **不覆盖同名文件**：`销售周报-2026-09-28.xlsx` 存在 → 落成 `… (1).xlsx` → `… (2).xlsx`。
  用 `O_CREAT|O_EXCL` **抢名字**（抢到了才写），不是"先看存在不存在再写"（那中间有窗口）。

════════════════════════════════════════════════════════════════════════
【"在文件夹中打开"只认**刚刚生成的那个文件**】
════════════════════════════════════════════════════════════════════════
`remember_saved` 只留**最后一条**记录（单用户自用，不建历史库 —— 评审明确禁止"历史导出管理"）。
`reveal_in_file_manager` 的目标路径**只能来自这条记录**，命令行是
`explorer.exe /select,"<path>"`，路径由本模块自己拼。**任何请求都不能把路径或命令传进来**。
非 Windows 平台明确返回"不支持"，不假装能用。
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

# 显式覆盖（运维/测试用）：设了它就用它，但**照样要过 is_dir() 验证**。
DESKTOP_DIR_ENV = "SRA_DESKTOP_DIR"

# 文件名里禁止出现的字符（Windows 保留字符）+ 目录分隔符
_FORBIDDEN_CHARS = '<>:"/\\|?*'

# Windows 保留设备名（这些名字做文件名会直接失败或被系统吃掉）
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                   "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}

_MAX_NAME_CHARS = 120          # 名字长度上限（给目录前缀和扩展名留余地，避免超长路径）
_MAX_DUPLICATE_TRIES = 1000    # 同名递增 (1)(2)… 的上限，撞满了就报错而不是无限转
_TEMP_PREFIX = ".sra-export-"  # 临时文件前缀（同目录，隐藏感 + 一眼认得出是半成品）
_TEMP_SUFFIX = ".part"

# 响应里如实标注的部署前提（不写清楚就可能被当成通用机制）
DEPLOYMENT_NOTE = (
    "本机单用户部署：服务端进程与用户桌面同机、同 Windows 会话（127.0.0.1 访问），"
    "所以能把文件直接写到你的桌面。换到其它机器部署时这条能力不成立。"
)


class DesktopError(Exception):
    """带机器可读 `code` 的领域错误（与 datasets/queries 的 QueryError 同形）。

    刻意**不 import** FastAPI：本模块是纯逻辑，HTTP 翻译由 app/api_exports.py 负责。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# ① 解析桌面目录（系统机制 → is_dir 验证 → 拿不到就报错）
# ════════════════════════════════════════════════════════════════════════
def _known_folder_desktop() -> Path | None:
    """`SHGetKnownFolderPath(FOLDERID_Desktop)` —— Windows 外壳的权威答案。

    OneDrive 重定向 / 用户改过桌面位置 / 中文 Windows，它返回的都是**真实生效**的那个目录。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
        guid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                     (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
        # ★ 导出这个函数的是 **shell32.dll**，不是 ole32.dll（写成 ole32 会直接
        #   `function 'SHGetKnownFolderPath' not found` —— 实测踩过）。ole32 只负责
        #   释放它分配的内存（CoTaskMemFree）。
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                 ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long          # HRESULT
        pointer = ctypes.c_void_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                        ctypes.byref(pointer)) != 0:
            return None
        try:
            text = ctypes.wstring_at(pointer) if pointer.value else ""
        finally:
            ole32 = ctypes.windll.ole32
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole32.CoTaskMemFree(pointer)     # 系统分配的内存，必须还回去
        return Path(text) if text else None
    except Exception:                        # noqa: BLE001 —— 探测失败就换下一个机制
        return None


def _registry_desktop() -> Path | None:
    """注册表 `User Shell Folders\\Desktop`（REG_EXPAND_SZ，值里可能带 `%USERPROFILE%`）。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key_path = (r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, "Desktop")
        text = os.path.expandvars(str(value)).strip()
        return Path(text) if text else None
    except Exception:                        # noqa: BLE001
        return None


def _shfolder_desktop() -> Path | None:
    """`SHGetFolderPathW(CSIDL_DESKTOPDIRECTORY)` —— 前两个机制都不可用时的兜底。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        shell32 = ctypes.windll.shell32
        shell32.SHGetFolderPathW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                             ctypes.c_uint32, ctypes.c_wchar_p]
        shell32.SHGetFolderPathW.restype = ctypes.c_long              # HRESULT
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        if shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) != 0:
            return None
        text = buffer.value.strip()
        return Path(text) if text else None
    except Exception:                        # noqa: BLE001
        return None


# 机制清单：**只有这三个**，顺序就是优先级（探测函数可被测试替换）
_SYSTEM_PROBES: tuple[Callable[[], Path | None], ...] = (
    _known_folder_desktop,
    _registry_desktop,
    _shfolder_desktop,
)


def resolve_desktop_dir(probes: tuple[Callable[[], Path | None], ...] | None = None) -> Path:
    """解析用户桌面目录。拿不到就抛 `desktop_unavailable` —— **不静默退回别处**。

    `probes` 只在测试里传（默认就是上面那三个系统机制）。
    """
    override = os.environ.get(DESKTOP_DIR_ENV)
    if override:
        directory = Path(override)
        if not _is_dir(directory):
            raise DesktopError(
                "desktop_not_a_directory",
                f"{DESKTOP_DIR_ENV} 指向的路径不是一个目录：{override} —— 不落盘到别处。",
            )
        return directory

    for probe in (probes if probes is not None else _SYSTEM_PROBES):
        try:
            candidate = probe()
        except Exception:                    # noqa: BLE001 —— 单个机制出错不该拖垮整条链
            continue
        if candidate is not None and _is_dir(candidate):
            return candidate

    raise DesktopError(
        "desktop_unavailable",
        "拿不到本机桌面目录：Windows 外壳 API（SHGetKnownFolderPath）、注册表 "
        "User Shell Folders、SHGetFolderPath 三个系统机制都没给出可用目录。"
        "文件**没有**写到别的地方 —— 请确认当前是可交互的 Windows 会话，"
        f"或用环境变量 {DESKTOP_DIR_ENV} 显式指定一个目录。",
    )


def _is_dir(path: Path) -> bool:
    """`is_dir()` 包一层：权限/路径异常一律当作"不是目录"，不往上抛裸 OSError。"""
    try:
        return path.is_dir()
    except OSError:
        return False


# ════════════════════════════════════════════════════════════════════════
# ② 文件名清洗 + 不覆盖 + 原子写
# ════════════════════════════════════════════════════════════════════════
def sanitize_filename(name: str, *, fallback: str = "导出文件") -> str:
    """导出文件名 → 可以安全落在桌面上的名字。

    做四件事（都只针对**文件名部分**，不碰目录）：
      ① 只取最后一段：`..\\..\\x.xlsx` / `/etc/passwd` 这类一律截成 `x.xlsx` / `passwd`
      ② 禁用字符 `< > : " / \\ | ? *` 与控制字符 → `_`
      ③ `..` 折叠掉、首尾的点和空格去掉（Windows 不许文件名以点或空格结尾）
      ④ 保留设备名（CON/PRN/NUL/COM1…）加前缀 `_`，并截断到 120 字符以内

    清洗是**纯函数**：同样的输入永远得到同样的输出，不依赖文件系统。
    """
    raw = str(name or "")
    # ① 反斜杠先归一成斜杠，再取最后一段（`..\\a.xlsx` → `a.xlsx`）
    raw = raw.replace("\\", "/").split("/")[-1]
    # ② 禁用字符 + 控制字符 → `_`
    cleaned = "".join("_" if (char in _FORBIDDEN_CHARS or ord(char) < 32) else char
                      for char in raw)
    # ③ `..` 折叠 + 去首尾的点和空格
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    cleaned = cleaned.strip().strip(".").strip()
    if not cleaned:
        cleaned = fallback

    stem, dot, suffix = cleaned.rpartition(".")
    if not dot:                              # 没有扩展名 → 整个都是 stem
        stem, suffix = cleaned, ""
    if stem.upper() in _RESERVED_NAMES:      # ④ 保留设备名
        stem = f"_{stem}"

    room = _MAX_NAME_CHARS - (len(suffix) + 1 if suffix else 0)
    stem = (stem or fallback)[: max(1, room)]
    result = f"{stem}.{suffix}" if suffix else stem
    return result or fallback


def _claim_target(directory: Path, filename: str) -> Path:
    """在同名文件旁边抢一个**还没被占用**的名字：`x.xlsx` → `x (1).xlsx` → `x (2).xlsx`。

    用 `O_CREAT|O_EXCL` 真去**建**这个文件：建得成 = 名字归我（并发下也不会两个请求拿同一个名字）。
    建出来的空文件随后会被 `os.replace` 原子替换成真内容；失败时由调用方清掉。
    """
    stem, suffix = os.path.splitext(filename)
    for index in range(_MAX_DUPLICATE_TRIES):
        candidate = directory / (filename if index == 0 else f"{stem} ({index}){suffix}")
        try:
            handle = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        except OSError as exc:
            raise DesktopError(
                "desktop_write_failed",
                f"在桌面建文件失败（{candidate.name}）：{exc.strerror or exc}",
            ) from exc
        os.close(handle)
        return candidate
    raise DesktopError(
        "desktop_name_exhausted",
        f"桌面上「{filename}」的重名副本已经排到 {_MAX_DUPLICATE_TRIES} 个了，没法再取新名字。",
    )


def _write_temp(directory: Path, content: bytes) -> Path:
    """在**同一个目录**写临时文件（同卷 rename 才可能是原子的）。"""
    temp_name: str | None = None
    try:
        handle, temp_name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX,
                                             dir=str(directory))
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
        return Path(temp_name)
    except OSError as exc:
        # mkstemp 成功但写失败：临时文件已经落在桌面上了，这里就地清掉（不留残渣）
        if temp_name is not None:
            _unlink_quietly(Path(temp_name))
        raise DesktopError(
            "desktop_write_failed",
            f"往桌面写临时文件失败：{exc.strerror or exc}",
        ) from exc


def _unlink_quietly(path: Path) -> None:
    """清理失败路径上的残留：删不掉也不该盖住真正的错误，所以吞掉 OSError。"""
    try:
        os.unlink(str(path))
    except OSError:
        pass


def save_to_desktop(filename: str, content: bytes,
                    *, now: _dt.datetime | None = None) -> dict[str, Any]:
    """把一份导出内容落到桌面 → 返回这次落盘的**如实**元信息。

    流程：清洗文件名 → 抢一个不重名的目标 → 同目录写临时文件 → `os.replace` 原子改名。
    任何一步失败：临时文件与抢到的占位文件都清掉，异常照原样抛出去（**不产出半截文件**）。
    """
    directory = resolve_desktop_dir()
    safe_name = sanitize_filename(filename)
    target = _claim_target(directory, safe_name)
    temp_path: Path | None = None
    try:
        temp_path = _write_temp(directory, content)
        os.replace(str(temp_path), str(target))
        temp_path = None                     # 改名成功：临时文件已经不存在了
    except DesktopError:
        _unlink_quietly(target)
        raise
    except OSError as exc:
        if temp_path is not None:
            _unlink_quietly(temp_path)
        _unlink_quietly(target)
        raise DesktopError(
            "desktop_write_failed",
            f"写入桌面失败（{target.name}）：{exc.strerror or exc}",
        ) from exc
    except BaseException:
        if temp_path is not None:
            _unlink_quietly(temp_path)
        _unlink_quietly(target)
        raise

    stamp = (now or _dt.datetime.now()).replace(microsecond=0).isoformat(sep=" ")
    record = {
        "export_id": uuid.uuid4().hex[:16],
        "file_name": target.name,            # 清洗 + 防重之后**真正**落地的名字
        "path": str(target),
        "desktop_dir": str(directory),
        "bytes": len(content),
        "saved_at": stamp,
        "deployment": DEPLOYMENT_NOTE,
    }
    _remember(record)
    return record


# ════════════════════════════════════════════════════════════════════════
# ③ "刚刚生成的那个文件" —— 只留一条记录（不是历史导出管理）
# ════════════════════════════════════════════════════════════════════════
_LAST_SAVED: dict[str, Any] | None = None


def _remember(record: dict[str, Any]) -> None:
    global _LAST_SAVED
    _LAST_SAVED = dict(record)


def last_saved() -> dict[str, Any] | None:
    """最近一次落盘的那条记录（没有就 None）。返回副本，调用方改不动内部状态。"""
    return dict(_LAST_SAVED) if _LAST_SAVED else None


def reset_last_saved() -> None:
    """清掉"最近一次"记录（测试用）。"""
    global _LAST_SAVED
    _LAST_SAVED = None


# ════════════════════════════════════════════════════════════════════════
# ④ 在资源管理器里定位（只认刚刚生成的那个文件）
# ════════════════════════════════════════════════════════════════════════
def reveal_supported() -> bool:
    """「在文件夹中打开」只在 Windows 本机有实现；别的平台**如实说不支持**。"""
    return sys.platform == "win32"


def reveal_in_file_manager(path: Path) -> None:
    """`explorer.exe /select,"<path>"` —— 打开文件夹并选中这个文件。

    `path` **只能**由本模块从 `last_saved()` 里取（调用方 = api_exports），
    任何来自请求的路径/命令都不许走到这里 —— 参数是 `Path`，不拼 shell 字符串。
    """
    if not reveal_supported():
        raise DesktopError(
            "reveal_unsupported",
            f"「在文件夹中打开」只在 Windows 上可用，当前系统是 {sys.platform} —— 这个功能没有实现，不假装可用。",
        )
    if not path.is_file():
        raise DesktopError(
            "file_missing",
            f"刚才那个文件已经不在了（{path.name}，可能被移动或删除），没法定位。",
        )
    # 纵深防御：万一桌面目录里混进了引号/换行（我们自己的清洗不会产生它们），
    # 宁可报错也不拼出一条能改变参数含义的命令行。
    text = str(path)
    if any(char in text for char in '"\r\n'):
        raise DesktopError("reveal_failed", "目标路径里有不该出现的字符，拒绝拼进命令行。")

    # `/select,` 与路径必须是同一段参数（explorer 自己解析引号），写成两个 argv 会认不出来
    command = f'/select,"{text}"'
    try:
        subprocess.Popen(["explorer.exe", command])
    except OSError as exc:
        raise DesktopError(
            "reveal_failed",
            f"调不起资源管理器：{exc.strerror or exc}",
        ) from exc


__all__ = [
    "DEPLOYMENT_NOTE",
    "DESKTOP_DIR_ENV",
    "DesktopError",
    "last_saved",
    "reset_last_saved",
    "resolve_desktop_dir",
    "reveal_in_file_manager",
    "reveal_supported",
    "sanitize_filename",
    "save_to_desktop",
]

"""原生「选择文件夹」对话框（本机桌面应用场景）。

前端是浏览器 / 内嵌预览，`<input type="file">` 拿不到绝对路径，
File System Access API 也只给句柄不给路径，所以由后端在本机弹出系统对话框：

- Windows：PowerShell + System.Windows.Forms.FolderBrowserDialog（即「选择文件夹」窗口）
- macOS：osascript 的 choose folder
- Linux：zenity --file-selection --directory

约定：
- 用户点取消 / 直接关窗 → 返回空字符串（调用方据此不做任何改动）；
- 后端与浏览器在同一台机器（本地工作台），对话框才会出现在用户面前；
- 同一时刻只允许一个对话框，避免重复点击叠出一堆窗口。

2026-11-28 修复：Windows 上对话框没被前置，用户看不到（窗口压在浏览器后面），
再点「添加位置」只会收到「已有一个文件夹选择窗口在等待操作」。现在用一个
TopMost 的不可见 owner 窗口代持，并用 EnumWindows/SetForegroundWindow 强制置顶。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

# 同一时刻只弹一个：第二次点击直接告知"已有对话框"
_dialog_lock = threading.Lock()
# 当前正在等待的对话框进程与开始时间（用于卡死自愈 + 给前端更准确的提示）
_active: dict[str, float | int | None] = {"proc": None, "started_at": 0.0}


class PickerUnavailable(RuntimeError):
    """当前平台没有可用的原生对话框。"""


class PickerBusy(RuntimeError):
    """已经有一个对话框在等待用户操作。"""


# Windows：FolderBrowserDialog（STA），owner 窗口 TopMost 保证置顶，输出 UTF-8，取消时无输出
_WIN_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null

if (-not ('AkaiFg' -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class AkaiFg {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);

  public static IntPtr FindDialog(uint pid) {
    IntPtr found = IntPtr.Zero;
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      uint p;
      GetWindowThreadProcessId(h, out p);
      if (p != pid) { return true; }
      if (!IsWindowVisible(h)) { return true; }
      StringBuilder sb = new StringBuilder(64);
      GetClassName(h, sb, 64);
      if (sb.ToString() == "#32770") { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }

  public static void Force(IntPtr h) {
    if (h == IntPtr.Zero) { return; }
    uint owner;
    uint tid = GetWindowThreadProcessId(h, out owner);
    uint cur = GetCurrentThreadId();
    AttachThreadInput(cur, tid, true);
    BringWindowToTop(h);
    SetForegroundWindow(h);
    AttachThreadInput(cur, tid, false);
  }
}
"@
}

$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.StartPosition = 'CenterScreen'
$owner.Show()
$owner.Activate()

# 对话框弹出后强制拉到最前（只做一次，避免跟用户抢焦点）
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({
    $h = [AkaiFg]::FindDialog([uint32]$PID)
    if ($h -ne [IntPtr]::Zero) {
        [AkaiFg]::Force($h)
        $timer.Stop()
    }
})
$timer.Start()

$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = '选择文件夹'
$dlg.ShowNewFolderButton = $true
$dlg.RootFolder = [System.Environment+SpecialFolder]::MyComputer

try {
    $res = $dlg.ShowDialog($owner)
} finally {
    $timer.Stop()
    $owner.Close()
    $owner.Dispose()
}
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Out.Write($dlg.SelectedPath)
}
"""

_MAC_SCRIPT = 'POSIX path of (choose folder with prompt "选择文件夹")'

_WIN_SCRIPT_FILE = os.path.join(tempfile.gettempdir(), "akai_pick_folder.ps1")


def _decode_console(raw: bytes) -> str:
    """Windows 控制台输出编码不稳（脚本内已设 UTF-8，PowerShell 自身的错误走本地代码页）。"""
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    if "\ufffd" in text:
        for enc in ("gbk", "mbcs"):
            try:
                alt = raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
            text = alt.lstrip("\ufeff")
            break
    return text.strip()


def available() -> bool:
    """当前平台是否支持原生对话框（供前端决定是否显示「选择」按钮）。"""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    try:
        subprocess.run(["which", "zenity"], capture_output=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def pick_folder(timeout: float = 300.0) -> str:
    """弹出系统文件夹选择框，返回绝对路径；用户取消返回空串。

    同一个进程内串行弹窗；上一个对话框已等待超过 timeout 视为卡死，
    直接回收并允许重新弹出（避免用户把窗口关到别的虚拟桌面后永远点不动）。
    """
    if not _dialog_lock.acquire(blocking=False):
        started = float(_active.get("started_at") or 0.0)
        proc = _active.get("proc")
        if started and time.monotonic() - started < timeout:
            raise PickerBusy("已有一个文件夹选择窗口在等待操作")
        # 卡死自愈：杀掉上一个子进程，等锁释放后重试一次
        if isinstance(proc, subprocess.Popen):
            try:
                proc.kill()
            except OSError:
                pass
        if not _dialog_lock.acquire(timeout=5.0):
            raise PickerBusy("已有一个文件夹选择窗口在等待操作")
    _active["started_at"] = time.monotonic()
    try:
        if sys.platform.startswith("win"):
            return _pick_windows(timeout)
        if sys.platform == "darwin":
            return _pick_macos(timeout)
        return _pick_linux(timeout)
    finally:
        _active["proc"] = None
        _active["started_at"] = 0.0
        _dialog_lock.release()


def _write_win_script() -> str:
    """把 PS 脚本落到临时文件（UTF-8 BOM）——比 -Command 传多行脚本稳，中文也不乱码。"""
    with open(_WIN_SCRIPT_FILE, "w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(_WIN_SCRIPT)
    return _WIN_SCRIPT_FILE


def _pick_windows(timeout: float) -> str:
    # CREATE_NO_WINDOW：不闪黑框；对话框由脚本内的 TopMost owner 负责置顶
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        script_path = _write_win_script()
    except OSError as exc:
        raise PickerUnavailable(f"无法写入选择框脚本：{exc}") from exc

    try:
        proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
    except FileNotFoundError as exc:
        raise PickerUnavailable("找不到 powershell，无法打开文件夹选择框") from exc

    _active["proc"] = proc
    try:
        out_bytes, err_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise PickerUnavailable("文件夹选择框等待超时，已关闭") from exc

    out = _decode_console(out_bytes)
    if proc.returncode != 0 and not out:
        msg = _decode_console(err_bytes)
        raise PickerUnavailable(msg[:300] or "打开文件夹选择框失败")
    return out


def _pick_macos(timeout: float) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-e", _MAC_SCRIPT],
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise PickerUnavailable("找不到 osascript") from exc
    except subprocess.TimeoutExpired as exc:
        raise PickerUnavailable("文件夹选择框等待超时") from exc
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    return out.rstrip("/") or out


def _pick_linux(timeout: float) -> str:
    try:
        proc = subprocess.run(
            ["zenity", "--file-selection", "--directory", "--title=选择文件夹"],
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise PickerUnavailable("未安装 zenity，无法打开文件夹选择框") from exc
    except subprocess.TimeoutExpired as exc:
        raise PickerUnavailable("文件夹选择框等待超时") from exc
    if proc.returncode != 0:  # 取消
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()

"""Windows 原生文件选择框 / 提示框。

命令行里没有给出文件时（例如双击启动器），用系统对话框让用户挑选。

两个坑必须避开：

1. **不要依赖管道的编码。** PowerShell 5.1 写 stdout 时用的是控制台输出编码
   （中文 Windows 默认 GBK），Python 若按 UTF-8 解码，中文路径就会被毁掉。
   所以这里让 PowerShell 把结果以 UTF-8 写进临时文件，Python 再读文件。
2. **不依赖 tkinter。** 目标机器不一定带 Tk，直接调 WinForms 更省事。

参数只通过命令行传给 PowerShell（Python 用 CreateProcessW，走的是 Unicode），
结果只通过文件回来，两端都不经过有编码歧义的管道。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def available() -> bool:
    return sys.platform == "win32"


def _run_ps(body: str) -> str | None:
    """执行一段 PowerShell 脚本，返回它设置的 `$result`。

    脚本里的 `$result` 由调用方赋值；结果经 UTF-8 临时文件回传。
    """
    if not available():
        return None

    handle, name = tempfile.mkstemp(prefix="tarch_dlg_", suffix=".txt")
    os.close(handle)
    try:
        script = (
            "$ProgressPreference = 'SilentlyContinue';"
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$result = '';"
            + body
            + "[IO.File]::WriteAllText("
              f"'{name}', [string]$result, (New-Object System.Text.UTF8Encoding($false)))"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=1800,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = Path(name).read_text("utf-8", errors="replace").strip()
        return text or None
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _ps_single(text: str) -> str:
    """转义单引号，便于嵌进 PowerShell 的单引号字符串。"""
    return (text or "").replace("'", "''")


def pick_file(title: str, filter_text: str) -> Path | None:
    """弹出「打开文件」对话框；用户取消时返回 None。"""
    body = (
        "$f = New-Object System.Windows.Forms.OpenFileDialog;"
        f"$f.Title = '{_ps_single(title)}';"
        f"$f.Filter = '{_ps_single(filter_text)}';"
        "$f.CheckFileExists = $true;"
        "$f.Multiselect = $false;"
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ $result = $f.FileName };"
    )
    result = _run_ps(body)
    return Path(result) if result else None


def pick_folder(title: str) -> Path | None:
    """弹出文件夹选择框；用户取消时返回 None。"""
    body = (
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$f.Description = '{_ps_single(title)}';"
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ $result = $f.SelectedPath };"
    )
    result = _run_ps(body)
    return Path(result) if result else None


def show_message(title: str, message: str) -> None:
    """弹一个提示框，用于双击启动时报告错误。"""
    body = (
        "[System.Windows.Forms.MessageBox]::Show("
        f"'{_ps_single(message)}', '{_ps_single(title)}',"
        " 'OK', 'Warning') | Out-Null;"
    )
    _run_ps(body)

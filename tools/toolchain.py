"""Shared discovery for optional developer tools; no installation-specific defaults."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def sgdk_tool(sdk, tool):
    """Use SGDK's Windows binaries or its native Unix tool names."""
    sdk = Path(sdk)
    if sys.platform == "win32":
        command = sdk / "bin" / (tool + ".exe")
        if command.is_file():
            return command
    else:
        name = os.environ.get("PREFIX", "m68k-elf-") + "gcc" if tool == "gcc" else tool
        command = shutil.which(name)
        if command:
            return Path(command).resolve()
    raise ValueError(
        f"SGDK tool missing: {tool}; "
        + (f"check {sdk / 'bin'} and GDK/--sgdk."
           if sys.platform == "win32" else
           f"install native {name} and add it to PATH. Windows .exe tools "
           "cannot be used for a native Linux build.")
    )


def sgdk_path(explicit=None):
    value = explicit or os.environ.get("GDK")
    if not value:
        raise ValueError("Set GDK to your SGDK directory, or pass --sgdk PATH.")
    path = Path(value).expanduser().resolve()
    for tool in ("gcc", "sjasm", "make"):
        sgdk_tool(path, tool)
    return path


def core_path(explicit="genesis_plus_gx"):
    value = str(explicit or "genesis_plus_gx")
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    directory = os.environ.get("CALLIOPE_CORES_DIR")
    suffix = (
        ".dll"
        if sys.platform == "win32"
        else ".dylib" if sys.platform == "darwin" else ".so"
    )
    if directory and candidate.name == value:
        filename = value if value.endswith(suffix) else value + "_libretro" + suffix
        candidate = Path(directory).expanduser() / filename
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(
        "Libretro core not found; pass --core PATH or set CALLIOPE_CORES_DIR."
    )


def furnace_path(explicit=None):
    value = explicit or os.environ.get("CALLIOPE_FURNACE") or shutil.which("furnace")
    if value and Path(value).is_file():
        return Path(value).resolve()
    raise ValueError("Furnace not found; pass --furnace PATH or set CALLIOPE_FURNACE.")


def msvc_path(explicit=None, arch="x86"):
    value = explicit or os.environ.get("CALLIOPE_VCVARS")
    if value:
        path = Path(value)
    else:
        locator = (
            Path(os.environ.get("ProgramFiles(x86)", ""))
            / "Microsoft Visual Studio/Installer/vswhere.exe"
        )
        if not locator.is_file():
            raise ValueError(
                "MSVC not found; install C++ build tools or pass --vcvars PATH."
            )
        install = subprocess.check_output(
            [
                str(locator),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            text=True,
        ).strip()
        path = (
            Path(install)
            / "VC/Auxiliary/Build"
            / ("vcvars64.bat" if arch == "x64" else "vcvars32.bat")
        )
    if not path.is_file():
        raise ValueError(
            "MSVC environment missing; pass --vcvars PATH or set CALLIOPE_VCVARS."
        )
    return path.resolve()

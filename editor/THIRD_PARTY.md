# Third-party components

The editor's preview runs Genesis Plus GX, pinned at
`393f70cd64c7334564c19a424c383f53986c13f4` from
https://github.com/libretro/Genesis-Plus-GX.

The core includes a Calliope modification exporting a read-only pointer to Z80
RAM. Its CPU and audio execution paths are unchanged. The complete upstream
source, Calliope telemetry hook, and CMake build files accompany the binary in
`assets/genesis-plus-gx-source.zip`. Extract it and configure CMake with
`-DCORE_SOURCE=/path/to/Genesis-Plus-GX` against `calliope-core-build` to rebuild.
The complete upstream license and component notices accompany it in
`assets/GENESIS-PLUS-GX-LICENSE.txt`. Preserve both when redistributing.

Genesis Plus GX restricts redistribution in commercial products or activities.
Read its complete license before redistributing the editor. This does not
change the Calliope bank format or attach emulator code to exported audio banks.

The four player ROM templates contain SGDK library code. Preserve the bundled
`assets/SGDK-LICENSE.txt` MIT notice. They contain Calliope's own player, adapter
and driver, and an empty bank placeholder. No example songs, game assets, or
console BIOS files are bundled in the player templates.

The desktop application uses Qt for Python / PySide6 and Shiboken, distributed
under their applicable LGPLv3/GPLv3/commercial terms. Standalone packages retain
Qt as separate shared libraries. Preserve the bundled license texts, permit
replacement of these libraries, and provide corresponding library source or
the applicable source offer when distributing under the LGPL. Qt source is at
https://code.qt.io/ and PySide source is at https://code.qt.io/pyside/pyside-setup.git/.

Qt deployment may also include audio backend dependencies. Keep the notices
shipped with the exact Qt wheel and audit the packaged dependency list before
publishing a release.

The editor preview host is independent of `tools/libretro_host.py`; that test
harness is not included in the standalone application.

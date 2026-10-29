# Incremental consumer builds

Calliope supports GNU Make 3.81. For an SGDK consumer, set `GDK` and
`CALLIOPE_HOME`, then use:

```make
CALLIOPE_BANK := audio/manifest.json
include $(CALLIOPE_HOME)/make/sgdk.mk
include $(CALLIOPE_HOME)/make/calliope.mk
```

The SGDK wrapper replaces a direct include of `$(GDK)/makefile.gen`. It creates
a private adapted copy under the build directory; it never changes the installed
SDK. It preserves SGDK compilation flags, retains the linker response file,
tracks rescomp inputs through persistent assembly and depfiles, and pads only
after linking. Resource depfiles are remade before C dependency decisions so
new resource headers are visible in the same build. Unsupported SGDK padding
rules fail explicitly instead of silently disabling incremental behavior.

The retained linker response tracks object membership and order, including
consumer filters applied after the SDK include. Adding or removing a source
updates the link. Release builds retain an unchanged padded ROM; debug builds
preserve the SDK's existing symbol-injection workflow.

Existing consumers that include SGDK directly still receive dependency-driven
Calliope preparation. Adopt the wrapper as well to avoid SGDK's unconditional
padding and repeated links. The supplied examples and audition project use it.

## Configuration and products

The public configuration variables and defaults are unchanged. The Make include
compares effective settings with the previously prepared settings without
launching Python. Changed mode overrides, configuration paths, bank manifests,
regions, symbols, wrapper options, SDK paths, and Python paths invalidate
preparation. Switching back to a previously used setting is also a change.

Actual generation records configuration-header includes, Python converter
dependencies, driver sources, bank source files, and executable inputs.
Configuration, driver assembly, wrappers, and bank products share a completion
stamp and output inventory. Deleting an output or its dependency metadata
regenerates it. Byte-identical products keep their timestamps, and a failed
preparation invalidates the stamp so the next build retries.

Linked-bank validation depends on the ROM, symbols, prepared bank,
configuration, and validator. It runs after padding and records successful
completion. An unchanged release build invokes no Calliope Python scripts and
does not rewrite its ROM.

Generated files belong to `CALLIOPE_BUILD_DIR`. Keep authored manifests,
configuration headers, Furnace files, and samples outside that directory.
Standalone converter/configuration commands remain supported. The public
driver cache still detects damaged cached bytes when preparation runs; ordinary
Make scheduling checks timestamps and existence rather than hashing every file
on every invocation.

## Recipe dependency recorder

`tools/make_step.py` is a reusable Python-recipe runner. Supply a stamp, depfile,
source roots, script, and its arguments. It observes I/O during the requested
generation, then writes the dependency file and output inventory after success.
There is no separate discovery execution. File reads from external tools can
be declared with `sys.audit('build.input', path)`. Content-aware output writers
must call `sys.audit('build.output', path)` before checking whether bytes changed.
Additional `--input` and `--output` arguments declare subprocess dependencies
and expected products that cannot be observed directly.

Use separate build directories for independent concurrent builds. Make targets
within one invocation can share a producer safely. GNU Make 3.81 has
whole-second timestamp resolution; edits must advance timestamps, and files
restored with historical timestamps require an explicit clean/rebuild.

The focused `test_make_step.py` and `test_build_config.py` tests cover idle
builds, input changes, identical regeneration, missing outputs and metadata,
directory membership, failures, parallel requests, nested headers, paths with
spaces, and mode/region switches. Set `GDK` before running SDK-dependent tests.

`test_sgdk_incremental.py` additionally builds generic consumers against the
installed SDK, checks resource/header recovery and source membership changes,
then verifies an idle release. Its `OUT_DIR` fixture models that alternative
layout using the installed SDK rather than requiring a second installation.

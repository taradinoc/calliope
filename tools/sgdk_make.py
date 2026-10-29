"""Adapt SGDK's Make integration without modifying the installed SDK.

Retain the linker response file, and pad ROMs only after a new link. These
changes let Calliope consumers have genuinely idle incremental release builds.
"""
import argparse
import re
from pathlib import Path


def adapt(text):
    rule = re.search(r'^padROM:[ \t]*(\$\((?:OUT|OUT_DIR)\))/rom\.bin[ \t]*\n(?P<recipe>(?:\t[^\n]*\n)+)', text, re.M)
    if not rule:
        raise ValueError('Unsupported SGDK padROM rule; update the incremental adapter')
    output = rule[1]
    text = text.replace('\t$(RM) '+output+'/cmd_\n', '')
    # Find again after removing the response-file cleanup above.
    rule = re.search(r'^padROM:[ \t]*(\$\((?:OUT|OUT_DIR)\))/rom\.bin[ \t]*\n(?P<recipe>(?:\t[^\n]*\n)+)', text, re.M)
    replacement = '''padROM: $(OUT)/calliope-pad.stamp
$(OUT)/calliope-pad.stamp: $(OUT)/rom.out $(OUT)/symbol.txt $(GDK)/bin/sizebnd.jar | $(OUT)/rom.bin
\t$(RM) -f $@
RECIPE\t$(ECHO) complete > $@
ifeq ($(wildcard $(OUT)/rom.bin),)
.PHONY: calliope-rom-missing
$(OUT)/calliope-pad.stamp: calliope-rom-missing
endif
'''
    replacement = replacement.replace('$(OUT)', output).replace('RECIPE', rule['recipe'])
    original_pad = rule[0]
    replacement = 'ifneq ($(filter debug Debug,$(MAKECMDGOALS)),)\n'+original_pad+'else\n'+replacement+'endif\n'
    text = text[:rule.start()]+replacement+text[rule.end():]
    response = '\t$(ECHO) "$(OBJS)" > '+output+'/cmd_\n'
    if response not in text:raise ValueError('Unsupported SGDK linker response rule')
    text = text.replace(response, response+'\t$(ECHO) "CLP_LINK_OBJECTS := $(CLP_LINK_VALUE)" > '+output+'/calliope-link-inputs.mk\n')
    # Resource depfiles describe the rescomp output, so changing a PNG/BIN runs
    # rescomp before assembling it. Remaking included depfiles restarts Make
    # before C dependency decisions, including after header size changes.
    text = text.replace('\t$(CP) $*.d '+output+'/$*.d\n\t$(RM) $*.d\n', '')
    text = text.replace('$(RESCOMP) $*.res $*.rs -dep '+output+'/$*.o', '$(RESCOMP) $*.res $*.rs -dep $*.rs')
    resources = """
# Compare the final object order after consumer/Calliope additions and filters.
CLP_LINK_INPUTS := $(OUT)/calliope-link-inputs.mk
$(CLP_LINK_INPUTS): ;
-include $(CLP_LINK_INPUTS)
# Secondary expansion does not inherit the parent's target-specific LIBMD.
CLP_SDK_LINK_LIBRARY := $(if $(strip $(LIBMD)),$(LIBMD),$(LIB)/$(if $(filter debug Debug,$(MAKECMDGOALS)),libmd_debug.a,libmd.a))
CLP_LINK_VALUE = $(strip $(OBJS) $(CLP_SDK_LINK_LIBRARY) $(LIBGCC))
$(OUT)/rom.out: $(CLP_SDK_LINK_LIBRARY) $(LIBGCC) $(GDK)/md.ld
CLP_LINK_MATCHES = $(and $(findstring $(CLP_LINK_VALUE),$(strip $(CLP_LINK_OBJECTS))),$(findstring $(strip $(CLP_LINK_OBJECTS)),$(CLP_LINK_VALUE)))
.PHONY: calliope-link-inputs-changed
.SECONDEXPANSION:
$(OUT)/cmd_: $$(if $$(CLP_LINK_MATCHES),,calliope-link-inputs-changed)
.SECONDARY: $(RES_RS)
$(RES_RS): $(GDK)/bin/rescomp.jar
$(RES_DEPS): $(OUT)/%.d: %.rs
\t$(MKDIR) -p $(dir $@)
\t$(CP) $*.d $@
$(RES_H): %.h: %.rs ;
CLP_MISSING_RESOURCE_DEPS := $(filter-out $(wildcard $(RES_DEPS)),$(RES_DEPS))
ifneq ($(strip $(CLP_MISSING_RESOURCE_DEPS)),)
.PHONY: calliope-resource-dependency-missing
$(patsubst $(OUT)/%.d,%.rs,$(CLP_MISSING_RESOURCE_DEPS)): calliope-resource-dependency-missing
endif
CLP_MISSING_RESOURCE_HEADERS := $(filter-out $(wildcard $(RES_H)),$(RES_H))
ifneq ($(strip $(CLP_MISSING_RESOURCE_HEADERS)),)
.PHONY: calliope-resource-header-missing
$(CLP_MISSING_RESOURCE_HEADERS:.h=.rs): calliope-resource-header-missing
endif
"""
    return text+resources.replace('$(OUT)', output)


def main():
    parser = argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('output');args = parser.parse_args()
    path = Path(args.output);path.parent.mkdir(parents=True, exist_ok=True)
    data = adapt(Path(args.source).read_text()).replace('\n', '\r\n').encode()
    if not path.exists() or path.read_bytes() != data:path.write_bytes(data)
    path.touch()


if __name__ == '__main__':main()

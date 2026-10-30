ifeq ($(strip $(.DEFAULT_GOAL)),)
.DEFAULT_GOAL := all
endif
# SGDK supplies its compilation rules. Built-in searches make large depfiles slow.
.SUFFIXES:
MAKEFLAGS += -r
# Include instead of SGDK makefile.gen, before make/calliope.mk.
ifndef CALLIOPE_HOME
CALLIOPE_HOME := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
endif
CALLIOPE_PYTHON ?= python
CALLIOPE_BUILD_DIR ?= out/calliope
CLP_SGDK_MAKE := $(CALLIOPE_BUILD_DIR)/sgdk.mk
$(CLP_SGDK_MAKE): $(GDK)/makefile.gen $(CALLIOPE_HOME)/tools/sgdk_make.py
	"$(CALLIOPE_PYTHON)" "$(CALLIOPE_HOME)/tools/sgdk_make.py" "$(GDK)/makefile.gen" "$@"
include $(CLP_SGDK_MAKE)

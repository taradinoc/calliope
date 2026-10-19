ifndef GDK
$(error Set GDK to your SGDK directory)
endif
CALLIOPE_HOME ?= $(abspath ..)
CALLIOPE_BANK ?= ../examples/bank.json
CALLIOPE_BUILD_DIR ?= ../out
include $(GDK)/makefile.gen
ifneq ($(strip $(OUT_DIR)),)
# Current SGDK supplies its own boot object and generated ROM header.
OBJS := $(filter-out $(OUT_DIR)/src/boot/%,$(OBJS))
$(OUT_DIR)/cmd_: Makefile
else
# Older SGDK embeds boot/rom_head.c in sega.o. A newer SDK may have left
# src/rom_header.c behind; do not link that second header as a normal source.
OBJS := $(filter-out $(OUT)/src/rom_header.o,$(OBJS))
$(OUT)/cmd_: Makefile
endif
include $(CALLIOPE_HOME)/make/calliope.mk

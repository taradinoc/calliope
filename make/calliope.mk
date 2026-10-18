# Include after SGDK makefile.gen. Mode defaults come from the public header;
# Makefile/command-line CALLIOPE_HYBRID=0/1 overrides its guarded defaults.
ifndef CALLIOPE_HOME
CALLIOPE_HOME := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
endif
CALLIOPE_PYTHON ?= python
CALLIOPE_CONFIG_HEADER ?= $(CALLIOPE_HOME)/include/calliope_config.h
CALLIOPE_BUILD_DIR ?= out/calliope
CALLIOPE_REGION ?= ntsc
CALLIOPE_BANK_SYMBOL ?= clp_bank
ifneq ($(strip $(CALLIOPE_BANK)),)
CALLIOPE_DRIVER_ASM ?= $(CALLIOPE_BUILD_DIR)/driver.s
CALLIOPE_SOURCE_WRAPPERS ?= 1
endif
CALLIOPE_DRIVER_ASM ?= src/calliope_driver.s
CLP_CONFIG_MK := $(CALLIOPE_BUILD_DIR)/calliope_build.mk
CLP_CONFIG_H := $(CALLIOPE_BUILD_DIR)/calliope_build.h
CALLIOPE_SOURCE_WRAPPERS ?= 0
CLP_CONFIG_ARGS :=
ifneq ($(strip $(CALLIOPE_BANK)),)
CLP_CONFIG_ARGS += --bank "$(CALLIOPE_BANK)" --region "$(CALLIOPE_REGION)" --symbol "$(CALLIOPE_BANK_SYMBOL)"
endif
ifeq ($(CALLIOPE_SOURCE_WRAPPERS),1)
CLP_CONFIG_ARGS += --source-wrappers "$(CALLIOPE_BUILD_DIR)"
endif
ifneq ($(origin CALLIOPE_HYBRID),undefined)
CLP_CONFIG_ARGS += --hybrid $(CALLIOPE_HYBRID)
endif
ifneq ($(origin CALLIOPE_HOST_PCM),undefined)
CLP_CONFIG_ARGS += --host-pcm $(CALLIOPE_HOST_PCM)
endif

# Resolve every invocation so command-line overrides cannot leave stale banks.
# The tool preserves timestamps when output contents have not changed, so the
# included makefile converges after one restart and incremental builds stay idle.
.PHONY: calliope-config-force calliope-config
calliope-config-force:
$(CLP_CONFIG_MK): calliope-config-force
	"$(CALLIOPE_PYTHON)" "$(CALLIOPE_HOME)/tools/configure.py" --sgdk "$(GDK)" --config-header "$(CALLIOPE_CONFIG_HEADER)" --output-dir "$(CALLIOPE_BUILD_DIR)" --driver-asm "$(CALLIOPE_DRIVER_ASM)" $(CLP_CONFIG_ARGS)
include $(CLP_CONFIG_MK)
calliope-config: $(CLP_CONFIG_MK)

CLP_OUT := $(if $(OUT),$(OUT),$(OUT_DIR))
CLP_OBJ_DIR := $(if $(OUT_DIR),$(OUT_DIR)/calliope,$(CLP_OUT)/$(CALLIOPE_BUILD_DIR))
CLP_CFLAGS := -I"$(CALLIOPE_HOME)/include" -include "$(CLP_CONFIG_H)" $(if $(strip $(CALLIOPE_BANK)),-I"$(CALLIOPE_BUILD_DIR)")
EXTRA_FLAGS += $(CLP_CFLAGS)
ifdef OUT_DIR
CFLAGS += $(CLP_CFLAGS)
AFLAGS += $(CLP_CFLAGS)
ifneq ($(wildcard $(SRC_LIB)/boot/rom_header.c),)
CLP_LEGACY_BOOT_OBJS := $(OUT_DIR)/$(SRC_DIR)/boot/sega.o $(OUT_DIR)/$(SRC_DIR)/boot/rom_head.o
OBJS := $(filter-out $(CLP_LEGACY_BOOT_OBJS),$(OBJS))
$(CLP_LEGACY_BOOT_OBJS):
	@$(MKDIR) -p $(dir $@)
	@: > $@
endif
$(DEPS): | $(CLP_CONFIG_MK)
$(CLP_OBJ_DIR)/%.o: $(CALLIOPE_BUILD_DIR)/%.c
	@$(MKDIR) -p $(dir $@)
	$(CC) $(CFLAGS) -MMD -MP -MF $(@:.o=.d) -c $< -o $@
$(CLP_OBJ_DIR)/%.o: $(CALLIOPE_BUILD_DIR)/%.s
	@$(MKDIR) -p $(dir $@)
	$(CC) $(AFLAGS) -c $< -o $@
endif
export CALLIOPE_HOME CALLIOPE_PYTHON CALLIOPE_PCM_TIMER
# C and assembly objects, plus rescomp, must be rebuilt after a mode switch.
CLP_RESOURCE_OBJS := $(addprefix $(CLP_OUT)/,$(RES_RES:.res=.o))
$(filter-out $(CLP_RESOURCE_OBJS),$(OBJS)) $(RES_RS): $(CLP_CONFIG_H)
# SGDK's initial wildcard may not see the driver on a fresh consumer checkout.
CLP_DRIVER_OBJ := $(if $(filter $(CALLIOPE_BUILD_DIR)/%,$(CALLIOPE_DRIVER_ASM)),$(CLP_OBJ_DIR)/$(notdir $(CALLIOPE_DRIVER_ASM:.s=.o)),$(CLP_OUT)/$(CALLIOPE_DRIVER_ASM:.s=.o))
OBJS := $(OBJS) $(filter-out $(OBJS),$(CLP_DRIVER_OBJ))
$(CLP_DRIVER_OBJ): $(CALLIOPE_DRIVER_ASM) $(CLP_CONFIG_H)
$(CLP_OUT)/rom.out: $(CLP_DRIVER_OBJ)
$(CLP_OUT)/cmd_: $(CLP_DRIVER_OBJ)

# Optional generated includes compile library sources in the consumer without
# copying library implementation files into the project checkout.
ifeq ($(CALLIOPE_SOURCE_WRAPPERS),1)
CLP_WRAPPER_OBJS := $(CLP_OBJ_DIR)/adapter.o $(CLP_OBJ_DIR)/bus.o $(CLP_OBJ_DIR)/dma.o
OBJS := $(OBJS) $(filter-out $(OBJS),$(CLP_WRAPPER_OBJS))
-include $(CLP_WRAPPER_OBJS:.o=.d)
$(CLP_OBJ_DIR)/bus.o: $(CALLIOPE_HOME)/src/hybrid_bus.s
$(CLP_WRAPPER_OBJS): $(CLP_CONFIG_H)
$(CLP_OUT)/rom.out: $(CLP_WRAPPER_OBJS)
$(CLP_OUT)/cmd_: $(CLP_WRAPPER_OBJS)
endif

# The direct-bank workflow owns all generated sources beneath the build dir.
# Configuration runs on every Make invocation; content-aware publication leaves
# timestamps unchanged when inputs have not changed.
ifneq ($(strip $(CALLIOPE_BANK)),)
CLP_BANK_OBJ := $(CLP_OBJ_DIR)/bank.o
OBJS := $(OBJS) $(filter-out $(OBJS),$(CLP_BANK_OBJ))
$(CLP_OUT)/cmd_: $(CLP_BANK_OBJ)
$(CLP_OUT)/rom.out: $(CLP_BANK_OBJ)
$(CLP_BANK_OBJ): $(CALLIOPE_BUILD_DIR)/bank.s $(CLP_CONFIG_H)
$(filter-out $(CLP_RESOURCE_OBJS),$(OBJS)): $(CALLIOPE_BUILD_DIR)/audio.h $(CALLIOPE_BUILD_DIR)/clp_ids.h
# SGDK pads the ROM first. Final validation runs after all release prerequisites.
ifdef OUT_DIR
release debug:
	@$(CP) "$(CLP_OUT)/symbol.txt" out/symbol.txt
	"$(CALLIOPE_PYTHON)" "$(CALLIOPE_HOME)/tools/consumer.py" --rom out/rom.bin --symbols out/symbol.txt --config "$(CALLIOPE_BUILD_DIR)/calliope_config.json" --symbol "$(CALLIOPE_BANK_SYMBOL)"
	@$(CP) out/rom.bin "$(CLP_OUT)/rom.bin"
else
release debug:
	"$(CALLIOPE_PYTHON)" "$(CALLIOPE_HOME)/tools/consumer.py" --rom "$(OUT)/rom.bin" --symbols "$(OUT)/symbol.txt" --config "$(CALLIOPE_BUILD_DIR)/calliope_config.json" --symbol "$(CALLIOPE_BANK_SYMBOL)"
endif
endif

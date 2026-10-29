# Include after SGDK makefile.gen. Mode defaults come from the public header;
# Makefile/command-line CALLIOPE_HYBRID=0/1 overrides its guarded defaults.
ifndef CALLIOPE_HOME
CALLIOPE_HOME := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
endif
# SGDK 2.11 uses OUT; newer SGDK separates release/debug with OUT_DIR.
CLP_OUT := $(if $(OUT_DIR),$(OUT_DIR),$(OUT))
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

# Compare effective Make settings without launching a process on idle builds.
CLP_SETTINGS := $(subst /,__slash__,$(subst :,__colon__,$(CALLIOPE_PYTHON)|$(GDK)|$(CALLIOPE_HOME)|$(CALLIOPE_CONFIG_HEADER)|$(CALLIOPE_BUILD_DIR)|$(CALLIOPE_DRIVER_ASM)|$(CLP_CONFIG_ARGS)))
export CALLIOPE_MAKE_SETTINGS := $(CLP_SETTINGS)
CLP_STEP := $(CALLIOPE_HOME)/tools/make_step.py
CLP_STAMP := $(CALLIOPE_BUILD_DIR)/configure.stamp
CLP_DEP := $(CALLIOPE_BUILD_DIR)/configure.d
CLP_INPUTS := $(addprefix $(CALLIOPE_HOME)/tools/,configure.py toolchain.py consumer.py clpconvert.py make_step.py) $(wildcard $(CALLIOPE_HOME)/tools/calliope/*.py $(CALLIOPE_HOME)/driver/*.s80 $(CALLIOPE_HOME)/driver/*.i80 $(GDK)/bin/gcc.exe $(GDK)/bin/sjasm.exe)
.PHONY: calliope-config
$(CLP_STAMP): $(CLP_INPUTS)
	"$(CALLIOPE_PYTHON)" "$(CLP_STEP)" --root "$(CALLIOPE_HOME)" --root "." --stamp "$@" --depfile "$(CLP_DEP)" "$(CALLIOPE_HOME)/tools/configure.py" --sgdk "$(GDK)" --config-header "$(CALLIOPE_CONFIG_HEADER)" --output-dir "$(CALLIOPE_BUILD_DIR)" --driver-asm "$(CALLIOPE_DRIVER_ASM)" $(CLP_CONFIG_ARGS)
$(CLP_CONFIG_MK) $(CLP_DEP): $(CLP_STAMP) ;
include $(CLP_CONFIG_MK)
-include $(CLP_DEP)
ifneq ($(CLP_PREPARED_SETTINGS),$(CLP_SETTINGS))
.PHONY: calliope-settings-changed
$(CLP_STAMP): calliope-settings-changed
endif
ifneq ($(words $(wildcard $(CLP_DEP) $(CLP_DEP).json)),2)
.PHONY: calliope-metadata-missing
$(CLP_STAMP): calliope-metadata-missing
endif
# Declare bootstrap outputs before depfiles exist, including parallel include remakes.
CLP_PREPARED_OUTPUTS := $(CLP_CONFIG_H) $(CALLIOPE_DRIVER_ASM) $(CALLIOPE_BUILD_DIR)/calliope_config.json $(CALLIOPE_BUILD_DIR)/driver.bin $(CALLIOPE_BUILD_DIR)/driver.lst $(CALLIOPE_BUILD_DIR)/calliope_driver.cache.json
ifeq ($(CALLIOPE_SOURCE_WRAPPERS),1)
CLP_PREPARED_OUTPUTS += $(addprefix $(CALLIOPE_BUILD_DIR)/,adapter.c bus.s dma.c)
endif
ifneq ($(strip $(CALLIOPE_BANK)),)
CLP_PREPARED_OUTPUTS += $(addprefix $(CALLIOPE_BUILD_DIR)/,bank.bin bank.s audio.h clp_ids.h bank.json)
endif
$(CLP_PREPARED_OUTPUTS): | $(CLP_STAMP)
calliope-config: $(CLP_STAMP)

CLP_FLAGS := -I"$(CALLIOPE_HOME)/include" -include "$(CLP_CONFIG_H)"
ifneq ($(strip $(CALLIOPE_BANK)),)
CLP_FLAGS += -I"$(CALLIOPE_BUILD_DIR)"
endif
# Newer SGDK freezes these flags before this include, so EXTRA_FLAGS alone
# cannot affect compilation. Older SGDK expands EXTRA_FLAGS lazily.
ifneq ($(strip $(OUT_DIR)),)
DEFAULT_FLAGS += $(CLP_FLAGS)
CFLAGS += $(CLP_FLAGS)
AFLAGS += $(CLP_FLAGS)
else
EXTRA_FLAGS += $(CLP_FLAGS)
endif
export CALLIOPE_HOME CALLIOPE_PYTHON CALLIOPE_PCM_TIMER
# C and assembly objects, plus rescomp, must be rebuilt after a mode switch.
CLP_RESOURCE_OBJS := $(addprefix $(CLP_OUT)/,$(RES_RES:.res=.o))
$(filter-out $(CLP_RESOURCE_OBJS),$(OBJS)) $(RES_RS): $(CLP_CONFIG_H)
# SGDK's initial wildcard may not see the driver on a fresh consumer checkout.
CLP_DRIVER_OBJ := $(CLP_OUT)/$(CALLIOPE_DRIVER_ASM:.s=.o)
OBJS := $(OBJS) $(filter-out $(OBJS),$(CLP_DRIVER_OBJ))
$(CLP_DRIVER_OBJ): $(CALLIOPE_DRIVER_ASM) $(CLP_CONFIG_H)
$(CLP_OUT)/rom.out: $(CLP_DRIVER_OBJ)
$(CLP_OUT)/cmd_: $(CLP_DRIVER_OBJ)

# Optional generated includes compile library sources in the consumer without
# copying library implementation files into the project checkout.
ifeq ($(CALLIOPE_SOURCE_WRAPPERS),1)
CLP_WRAPPER_OBJS := $(CLP_OUT)/$(CALLIOPE_BUILD_DIR)/adapter.o $(CLP_OUT)/$(CALLIOPE_BUILD_DIR)/bus.o $(CLP_OUT)/$(CALLIOPE_BUILD_DIR)/dma.o
OBJS := $(OBJS) $(filter-out $(OBJS),$(CLP_WRAPPER_OBJS))
-include $(CLP_WRAPPER_OBJS:.o=.d)
$(CLP_OUT)/$(CALLIOPE_BUILD_DIR)/bus.o: $(CALLIOPE_HOME)/src/hybrid_bus.s
$(CLP_WRAPPER_OBJS): $(CLP_CONFIG_H)
$(CLP_OUT)/rom.out: $(CLP_WRAPPER_OBJS)
$(CLP_OUT)/cmd_: $(CLP_WRAPPER_OBJS)
endif

# The direct-bank workflow owns all generated sources beneath the build dir.
# Configuration and bank preparation run only when their dependencies change.
ifneq ($(strip $(CALLIOPE_BANK)),)
CLP_BANK_OBJ := $(CLP_OUT)/$(CALLIOPE_BUILD_DIR)/bank.o
OBJS := $(OBJS) $(filter-out $(OBJS),$(CLP_BANK_OBJ))
$(CLP_OUT)/cmd_: $(CLP_BANK_OBJ)
$(CLP_OUT)/rom.out: $(CLP_BANK_OBJ)
$(CLP_BANK_OBJ): $(CALLIOPE_BUILD_DIR)/bank.s $(CLP_CONFIG_H)
$(filter-out $(CLP_RESOURCE_OBJS),$(OBJS)): $(CALLIOPE_BUILD_DIR)/audio.h $(CALLIOPE_BUILD_DIR)/clp_ids.h
# Order verification after SGDK padding, and remember successful verification.
CLP_VERIFY := $(CALLIOPE_BUILD_DIR)/verified.stamp
$(CLP_VERIFY): $(CLP_OUT)/rom.bin $(CLP_OUT)/symbol.txt $(CALLIOPE_BUILD_DIR)/calliope_config.json $(CALLIOPE_BUILD_DIR)/bank.bin $(CALLIOPE_HOME)/tools/consumer.py $(CALLIOPE_HOME)/tools/clpconvert.py | padROM
	$(RM) -f $@
	"$(CALLIOPE_PYTHON)" "$(CALLIOPE_HOME)/tools/consumer.py" --rom "$(CLP_OUT)/rom.bin" --symbols "$(CLP_OUT)/symbol.txt" --config "$(CALLIOPE_BUILD_DIR)/calliope_config.json" --symbol "$(CALLIOPE_BANK_SYMBOL)"
	$(ECHO) verified > $@
ifneq ($(filter debug Debug,$(MAKECMDGOALS)),)
$(CLP_VERIFY): injectSymbolsInROM
endif
release debug: $(CLP_VERIFY)
ifneq ($(CLP_OUT),out)
out/rom.bin: $(CLP_OUT)/rom.bin $(CLP_VERIFY)
	$(CP) "$(CLP_OUT)/rom.bin" "$@"
out/symbol.txt: $(CLP_OUT)/symbol.txt
	$(CP) "$<" "$@"
release debug: out/rom.bin out/symbol.txt
endif

endif

ifneq ($(strip $(DEP_DIR)),)
CLP_GENERATED_OBJS := $(CLP_DRIVER_OBJ) $(CLP_WRAPPER_OBJS) $(CLP_BANK_OBJ)
CLP_GENERATED_DEPS := $(patsubst $(CLP_OUT)/%.o,$(DEP_DIR)/%.d,$(CLP_GENERATED_OBJS))
# Configure before SGDK preprocesses dependencies, including on a region switch.
$(DEPS) $(CLP_GENERATED_DEPS): $(CLP_CONFIG_MK)
-include $(CLP_GENERATED_DEPS)
endif

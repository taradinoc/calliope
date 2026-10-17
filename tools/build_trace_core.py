"""Build a local instrumented validation core from the pinned upstream checkout."""
from toolchain import msvc_path
import argparse
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--vcvars',type=Path);a=p.parse_args()
    out=ROOT/'out/trace-core';out.mkdir(parents=True,exist_ok=True)
    original=(a.source/'core/sound/sound.c').read_text()
    declarations="""
/* Calliope local validation modification: register-write callback. */
typedef void (*clp_trace_fn)(unsigned int,unsigned int,unsigned int,unsigned int);
static clp_trace_fn clp_callback;
static unsigned int clp_address, clp_address1;
__declspec(dllexport) unsigned int clp_main_pc(void) { return m68k_get_reg(M68K_REG_PC); }
__declspec(dllexport) unsigned int clp_pcm_positions(void) { return ((unsigned int)Z80.iy.w.l<<16)|Z80.bc.w.l; }
__declspec(dllexport) unsigned char* clp_zram(void) { return zram; }
__declspec(dllexport) unsigned int clp_dac_fifo(void) { return ((Z80.iy.w.l>>8)<<8)|(Z80.bc.w.l&255); }
static clp_trace_fn clp_chip_callback;
void clp_psg_trace(unsigned int clocks,unsigned int data) { if (clp_chip_callback) clp_chip_callback(clocks,0x200,data,Z80.pc.w.l); }
__declspec(dllexport) void clp_chip_trace_set(clp_trace_fn fn) { clp_chip_callback=fn; }
__declspec(dllexport) void clp_trace_set(clp_trace_fn fn) { clp_callback=fn; }
static void clp_trace_write(unsigned int cycles,unsigned int a,unsigned int v) {
    if (a==0) clp_address=v;
    if (a==2) clp_address1=v;
    if ((a&1) && clp_chip_callback) clp_chip_callback(cycles,(a==1?clp_address:256+clp_address1),v,Z80.pc.w.l);
    if (a==1 && clp_address==0x2a && clp_callback) {
        unsigned int sp=Z80.sp.w.l&8191;
        clp_callback(cycles,v,Z80.pc.w.l,zram[sp]|((unsigned int)zram[(sp+1)&8191]<<8));
    }
}
"""
    original=original.replace('int8 audio_hard_disable',declarations+'\nint8 audio_hard_disable')
    for function in ('YM2612_Write','YM3438_Write'):
        signature='static void '+function+'(unsigned int cycles, unsigned int a, unsigned int v)\n{'
        assert signature in original
        original=original.replace(signature,signature+'\n  clp_trace_write(cycles,a,v);')
    (out/'sound.c').write_text(original,newline='\r\n')
    psg=(a.source/'core/sound/psg.c').read_text()
    signature='void psg_write(unsigned int clocks, unsigned int data)\n{'
    assert signature in psg
    psg=psg.replace(signature,'extern void clp_psg_trace(unsigned int,unsigned int);\n'+signature+'\n  clp_psg_trace(clocks,data);')
    (out/'psg.c').write_text(psg,newline='\r\n')
    cpu=(a.source/'core/z80/z80.c').read_text()
    cpu=cpu.replace('void z80_run(unsigned int cycles)', """static unsigned long long clp_cycles[8192];
static unsigned int clp_recent[2048], clp_recent_cycles[2048], clp_recent_index;
__declspec(dllexport) unsigned int* clp_history_cycles(void) { return clp_recent_cycles; }
__declspec(dllexport) unsigned int* clp_history(void) { return clp_recent; }
__declspec(dllexport) unsigned int clp_history_index(void) { return clp_recent_index; }
__declspec(dllexport) unsigned long long* clp_profile(void) { return clp_cycles; }
void z80_run(unsigned int cycles)""")
    cpu=cpu.replace('    EXEC_INLINE(op,ROP());', """    { unsigned pc=PCD&8191, start=Z80.cycles;
      clp_recent_cycles[clp_recent_index&2047]=start;
      clp_recent[clp_recent_index++&2047]=pc;
      EXEC_INLINE(op,ROP());
      clp_cycles[pc]+=(unsigned int)(Z80.cycles-start);
    }""")
    (out/'z80.c').write_text(cpu,newline='\r\n')
    # Count emulator instruction/bus cycles while the audition's service flag
    # is set. DAC callbacks are not uniformly spaced around bus acquisition.
    main_cpu=(a.source/'core/m68k/m68kcpu.c').read_text()
    anchor='#include "m68kops.h"'
    assert anchor in main_cpu
    main_cpu=main_cpu.replace(anchor,anchor+"""
extern unsigned char work_ram[65536];
static unsigned long long clp_audio_cycles[32768];
static unsigned int clp_audio_flag;
static int clp_audio_enabled;
__declspec(dllexport) void clp_audio_profile_reset(unsigned int flag) {
  memset(clp_audio_cycles,0,sizeof(clp_audio_cycles));
  clp_audio_enabled=flag<65535;clp_audio_flag=flag&65535;
}
__declspec(dllexport) const unsigned long long* clp_audio_profile_cycles(void) { return clp_audio_cycles; }
""")
    anchor='  while (m68k.cycles < cycles)\n  {'
    assert anchor in main_cpu
    main_cpu=main_cpu.replace(anchor,anchor+'\n    unsigned int clp_start=m68k.cycles, clp_pc=REG_PC;\n    unsigned int clp_audio_active=clp_audio_enabled && (work_ram[clp_audio_flag]|work_ram[clp_audio_flag+1]);')
    anchor='    m68ki_instruction_jump_table[REG_IR]();\n    USE_CYCLES(CYC_INSTRUCTION[REG_IR]);'
    assert main_cpu.count(anchor)==1
    main_cpu=main_cpu.replace(anchor,anchor+'\n    if (clp_audio_active && clp_pc<65536) clp_audio_cycles[clp_pc>>1] += m68k.cycles-clp_start;')
    (out/'m68kcpu.c').write_text(main_cpu,newline='\r\n')
    # Reuse the public pinned-core build, replacing only copied instrumented files.
    cmake=(ROOT/'editor/core/CMakeLists.txt').read_text()
    additions=[]
    for relative,name in (('core/sound/sound.c','sound.c'),('core/sound/psg.c','psg.c'),('core/z80/z80.c','z80.c'),('core/m68k/m68kcpu.c','m68kcpu.c')):
        additions += ['list(REMOVE_ITEM sources "${CORE_SOURCE}/'+relative+'")',
                      'list(APPEND sources "'+(out/name).as_posix()+'")']
    cmake=cmake.replace('add_library(genesis_plus_gx', '\n'.join(additions)+'\nadd_library(genesis_plus_gx')
    cmake=cmake.replace('genesis_plus_gx','calliope_trace')
    (out/'telemetry.c').write_bytes((ROOT/'editor/core/telemetry.c').read_bytes())
    (out/'CMakeLists.txt').write_text(cmake,newline='\r\n')
    vcvars=msvc_path(a.vcvars, 'x64')
    makers=sorted((vcvars.parents[2]/'Tools/MSVC').glob('*/bin/Hostx64/x64/nmake.exe'))
    if not makers:raise ValueError('MSVC nmake.exe is required for the trace core')
    batch='@echo off\ncall "'+str(vcvars)+'"\nif errorlevel 1 exit /b 1\n'
    batch+=f'cmake --fresh -S "{out}" -B "{out}/build" -G "NMake Makefiles" -DCMAKE_MAKE_PROGRAM="{makers[-1].as_posix()}" -DCMAKE_BUILD_TYPE=Release -DCORE_SOURCE="{a.source.resolve().as_posix()}"\nif errorlevel 1 exit /b 1\ncmake --build "{out}/build"\n'
    (out/'build.bat').write_text(batch,newline='\r\n')
    subprocess.run(['cmd','/c',str(out/'build.bat')],check=True)
if __name__=='__main__':main()

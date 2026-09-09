#!/usr/bin/env python3

"""
Build & Automation Script for the Tang Nano 9K SDCard / WS2812B LED Player.

Two targets are available (--target):
  sdcard    hardware/soc_tang_nano_9k_sdcard.py    -- lists the SDCard root dir over UART
  led_anim  hardware/soc_tang_nano_9k_led_anim.py  -- (default) plays a .fled animation from
                                                       the SDCard out to a WS2812B strip on
                                                       J7 pin 49, via an autonomous DMA core

Both targets skip the interactive LiteX BIOS entirely: our firmware is linked
BIOS-style (executes in place from ROM) and embedded directly as the ROM's
initial content via `integrated_rom_init`, so there's no separate main_ram
and no serialboot step. That means the build is two-phase:
  1. Generate the SoC once *without* a firmware binary, just to produce the
     `generated/csr.h` etc. headers the firmware needs to compile against
     (`--no-compile-gateware`, so this phase is fast).
  2. Compile the firmware against those headers, then regenerate the SoC
     *with* `--firmware-bin <path>` and run the full Gowin synthesis.

Everything is SRAM-only (JTAG) programming -- nothing is ever written to the
board's SPI flash, so a bad build only requires re-programming, never a
recovery flash.

See CLAUDE.md for the design-rationale history (why VexRiscv-minimal not
Ibex, the on-chip memory budget, etc).
"""

import os
import sys
import subprocess
import shutil
import argparse
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR    = os.path.join(PROJECT_ROOT, "build", "sipeed_tang_nano_9k")
HARDWARE_DIR = os.path.join(PROJECT_ROOT, "hardware")

TARGETS = {
    "sdcard":   {
        "soc_script":  os.path.join(HARDWARE_DIR, "soc_tang_nano_9k_sdcard.py"),
        "fw_dir":      os.path.join(PROJECT_ROOT, "firmware_sdcard_ls"),
    },
    "led_anim": {
        "soc_script":  os.path.join(HARDWARE_DIR, "soc_tang_nano_9k_led_anim.py"),
        "fw_dir":      os.path.join(PROJECT_ROOT, "firmware_led_anim"),
    },
}

def find_litex_python():
    """Find a Python interpreter that has LiteX installed."""
    try:
        import litex  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    candidates = [
        os.environ.get("LITEX_PYTHON"),
        os.path.join(os.environ.get("VIRTUAL_ENV", ""), "bin", "python3"),
        os.path.join(PROJECT_ROOT, ".venv", "bin", "python3"),
        os.path.abspath(os.path.join(PROJECT_ROOT, "..", "litex-venv", "bin", "python3")),
        os.path.expanduser("~/litex-venv/bin/python3"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            try:
                res = subprocess.run([cand, "-c", "import litex"], capture_output=True)
                if res.returncode == 0:
                    return cand
            except Exception:
                pass
    return None

LITEX_PYTHON = find_litex_python()
if LITEX_PYTHON and os.path.abspath(sys.executable) != os.path.abspath(LITEX_PYTHON):
    os.execv(LITEX_PYTHON, [LITEX_PYTHON] + sys.argv)
elif not LITEX_PYTHON:
    print("[WARN] No Python environment with 'litex' found in standard locations.")
    print("       Set LITEX_PYTHON=/path/to/python3 (a venv with litex/litex-boards/migen installed).\n")

def find_riscv_toolchain():
    """Finds a compatible RISC-V GCC toolchain directory and target triple."""
    triples = [
        "riscv-none-elf",
        "riscv32-esp-elf",
        "riscv32-unknown-elf",
        "riscv64-unknown-elf",
        "riscv32-none-elf",
    ]

    local_bin = os.path.join(PROJECT_ROOT, "toolchain", "riscv", "bin")
    if os.path.isdir(local_bin):
        for triple in triples:
            gcc = os.path.join(local_bin, f"{triple}-gcc")
            if os.path.isfile(gcc) and os.access(gcc, os.X_OK):
                return local_bin, triple

    env_toolchain = os.environ.get("RISCV_TOOLCHAIN_PATH") or os.environ.get("RISCV_BIN")
    if env_toolchain:
        bin_dir = env_toolchain if os.path.basename(env_toolchain) == "bin" else os.path.join(env_toolchain, "bin")
        for triple in triples:
            gcc = os.path.join(bin_dir, f"{triple}-gcc")
            if os.path.isfile(gcc) and os.access(gcc, os.X_OK):
                return bin_dir, triple

    for triple in triples:
        p = shutil.which(f"{triple}-gcc")
        if p:
            return os.path.dirname(p), triple

    return None, None

def find_gowin_tools():
    """Finds gw_sh and programmer_cli binaries dynamically."""
    gw_dir = None
    prog_cli = None

    env_gowin = os.environ.get("GOWIN_HOME") or os.environ.get("GOWIN_DIR") or os.environ.get("GOWIN_PATH")
    if env_gowin:
        for cand_ide in [os.path.join(env_gowin, "IDE", "bin"), os.path.join(env_gowin, "bin")]:
            if os.path.isfile(os.path.join(cand_ide, "gw_sh")):
                gw_dir = cand_ide
                break
        cand_prog = os.path.join(env_gowin, "Programmer", "bin", "programmer_cli")
        if os.path.isfile(cand_prog):
            prog_cli = cand_prog

    if not gw_dir:
        p = shutil.which("gw_sh")
        if p:
            gw_dir = os.path.dirname(p)
            guessed_prog = os.path.abspath(os.path.join(gw_dir, "..", "..", "Programmer", "bin", "programmer_cli"))
            if os.path.isfile(guessed_prog):
                prog_cli = guessed_prog

    if not prog_cli:
        p = shutil.which("programmer_cli")
        if p:
            prog_cli = p

    if not gw_dir or not prog_cli:
        for base in ["/opt/GowinEDA", os.path.expanduser("~/GowinEDA"), "/opt/gowin"]:
            cand_gw = os.path.join(base, "IDE", "bin", "gw_sh")
            cand_prog = os.path.join(base, "Programmer", "bin", "programmer_cli")
            if not gw_dir and os.path.isfile(cand_gw):
                gw_dir = os.path.dirname(cand_gw)
            if not prog_cli and os.path.isfile(cand_prog):
                prog_cli = cand_prog

    return gw_dir, prog_cli

RISCV_BIN_DIR, RISCV_TRIPLE = find_riscv_toolchain()
GOWIN_BIN_DIR, GOWIN_PROG_CLI = find_gowin_tools()

ENV = os.environ.copy()
py_bin_dir = os.path.dirname(sys.executable)
path_prefixes = [p for p in [RISCV_BIN_DIR, GOWIN_BIN_DIR, py_bin_dir] if p]
if path_prefixes:
    ENV["PATH"] = ":".join(path_prefixes) + ":" + ENV.get("PATH", "")
if RISCV_TRIPLE:
    ENV["LITEX_ENV_CC_TRIPLE"] = RISCV_TRIPLE

# Gowin's bundled libz.so.1 is older than what the system libpng16 needs;
# preload the system one (and freetype, for the GUI-less programmer_cli) or
# programmer_cli segfaults on startup.
for freetype_cand in [
    "/usr/lib/x86_64-linux-gnu/libfreetype.so.6",
    "/usr/lib64/libfreetype.so.6",
    "/usr/lib/libfreetype.so.6",
]:
    if os.path.exists(freetype_cand):
        libz_cand = freetype_cand.replace("libfreetype.so.6", "libz.so.1")
        preload = [freetype_cand] + ([libz_cand] if os.path.exists(libz_cand) else [])
        ENV["LD_PRELOAD"] = ":".join(preload)
        break

ENV["QT_QPA_PLATFORM"] = "minimal"

def run_cmd(cmd, cwd=PROJECT_ROOT, check=True, env=None):
    print(f"\n[EXEC] {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    run_env = ENV if env is None else env
    res = subprocess.run(cmd, cwd=cwd, env=run_env, shell=isinstance(cmd, str))
    if check and res.returncode != 0:
        print(f"[ERROR] Command failed with exit code {res.returncode}")
        sys.exit(res.returncode)
    return res.returncode

def step_check_environment():
    print("==========================================================")
    print(" STEP 1: Checking Toolchain Environment")
    print("==========================================================")
    gcc_bin = f"{RISCV_TRIPLE}-gcc" if RISCV_TRIPLE else "riscv-gcc"
    gcc_path = shutil.which(gcc_bin, path=ENV["PATH"])
    gw_path  = shutil.which("gw_sh", path=ENV["PATH"])

    print(f" -> RISC-V Triple       : {RISCV_TRIPLE or 'NOT FOUND'}")
    print(f" -> RISC-V GCC Compiler : {gcc_path or 'NOT FOUND'}")
    print(f" -> Gowin EDA Shell     : {gw_path or 'NOT FOUND'}")
    print(f" -> Gowin Programmer    : {GOWIN_PROG_CLI or 'NOT FOUND'}")

    if not gcc_path:
        print(f"[FAIL] {gcc_bin} compiler not found! Set RISCV_TOOLCHAIN_PATH or add it to PATH.")
        sys.exit(1)
    if not gw_path:
        print("[FAIL] Gowin EDA gw_sh not found! Please set GOWIN_HOME or add it to PATH.")
        sys.exit(1)
    print("[SUCCESS] Toolchain environment verified.\n")

def step_generate_headers(target):
    print("==========================================================")
    print(f" STEP 2: Generating LiteX SoC Headers ({target})")
    print("==========================================================")
    soc_script = TARGETS[target]["soc_script"]
    run_cmd([sys.executable, soc_script, "--toolchain", "gowin", "--no-compile-gateware", "--build"])
    print("[SUCCESS] SoC headers generated.\n")

def step_compile_firmware(target):
    print("==========================================================")
    print(f" STEP 3: Compiling RISC-V Firmware ({target})")
    print("==========================================================")
    fw_dir = TARGETS[target]["fw_dir"]
    run_cmd(["make", "clean"], cwd=fw_dir)
    run_cmd(["make"], cwd=fw_dir)

    fw_bin = os.path.join(fw_dir, "firmware.bin")
    if os.path.exists(fw_bin):
        size = os.path.getsize(fw_bin)
        print(f"[SUCCESS] Firmware binary compiled: {fw_bin} ({size} bytes)\n")
    else:
        print("[FAIL] firmware.bin not created!")
        sys.exit(1)
    return fw_bin

def step_build_bitstream(target, fw_bin):
    print("==========================================================")
    print(f" STEP 4: Building Bitstream with Embedded Firmware ({target})")
    print("==========================================================")
    soc_script = TARGETS[target]["soc_script"]
    run_cmd([sys.executable, soc_script, "--toolchain", "gowin", "--firmware-bin", fw_bin, "--build"])
    print("[SUCCESS] Gowin FPGA synthesis finished.\n")

def step_program_sram():
    print("==========================================================")
    print(" STEP 5: Programming Bitstream to FPGA SRAM (Non-Persistent)")
    print("==========================================================")
    fs_file = os.path.join(BUILD_DIR, "gateware", "sipeed_tang_nano_9k.fs")
    if not os.path.exists(fs_file):
        print(f"[FAIL] Bitstream file not found at {fs_file}")
        sys.exit(1)

    prog_bin = GOWIN_PROG_CLI or shutil.which("programmer_cli")
    if not prog_bin:
        print("[FAIL] Gowin programmer_cli not found! Please ensure Gowin Programmer is installed.")
        sys.exit(1)

    cmd = [
        prog_bin,
        "--device", "GW1NR-9C",
        "--cable-index", "5",
        "--operation_index", "2",
        "--fsFile", fs_file,
    ]
    run_cmd(cmd)
    print("[SUCCESS] Bitstream programmed to SRAM.\n")
    print(" -> Waiting 2.5 seconds for USB-UART re-enumeration...")
    time.sleep(2.5)

def step_run_firmware(port="/dev/ttyUSB1", duration=15):
    print("==========================================================")
    print(" STEP 6: Capturing UART Console Output")
    print("==========================================================")
    import serial
    s = serial.Serial(port, 115200, timeout=0.2)
    data = b""
    start = time.time()
    while time.time() - start < duration:
        chunk = s.read(4096)
        if chunk:
            data += chunk
        else:
            time.sleep(0.1)
    s.close()
    print(data.decode(errors="replace"))

def main():
    parser = argparse.ArgumentParser(description="Tang Nano 9K SDCard/LED Player Build & Runner")
    parser.add_argument("--target", choices=list(TARGETS.keys()), default="led_anim",
                         help="Which SoC target to build (default: led_anim).")
    parser.add_argument("--build-only", action="store_true", help="Only build headers, firmware, and bitstream.")
    parser.add_argument("--sram",       action="store_true", help="Program the last-built bitstream to SRAM only.")
    parser.add_argument("--run",        action="store_true", help="Program to SRAM and capture UART output.")
    parser.add_argument("--port",       default="/dev/ttyUSB1", help="Serial port for the firmware console.")
    args = parser.parse_args()

    if args.sram:
        step_program_sram()
        return

    if args.run:
        step_program_sram()
        step_run_firmware(port=args.port)
        return

    step_check_environment()
    step_generate_headers(args.target)
    fw_bin = step_compile_firmware(args.target)
    step_build_bitstream(args.target, fw_bin)

    if not args.build_only:
        step_program_sram()
        step_run_firmware(port=args.port)

if __name__ == "__main__":
    main()

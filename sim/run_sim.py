#!/usr/bin/env python3
"""
Automated Simulation Runner for the WS2812B Driver & LED Animator DMA
Controller using Cocotb and Verilator.

Features:
 - Automatic venv management (creates sim/.venv and installs requirements if needed)
 - Self-reexecuting into the virtual environment
 - Cycle-accurate Python-based verification with Cocotb & Verilator
 - Clean reporting and optional waveform generation (--waves)
"""

import os
import sys
import shutil
import subprocess
import argparse

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SIM_DIR, ".."))
VENV_DIR = os.path.join(SIM_DIR, ".venv")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python3")
REQUIREMENTS = os.path.join(SIM_DIR, "requirements.txt")

# ------------------------------------------------------------------------------
# 1. Virtual Environment Bootstrap
# ------------------------------------------------------------------------------
def ensure_venv():
    """Ensure virtual environment exists with dependencies, then re-exec into it."""
    if not os.path.exists(VENV_PYTHON):
        print(f"[*] Creating simulation virtualenv at {VENV_DIR}...")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
        print(f"[*] Installing cocotb and dependencies from {REQUIREMENTS}...")
        pip_path = os.path.join(VENV_DIR, "bin", "pip")
        subprocess.check_call([pip_path, "install", "--upgrade", "pip"])
        subprocess.check_call([pip_path, "install", "-r", REQUIREMENTS])
        print("[*] Virtual environment setup complete.\n")

    current_python = os.path.abspath(sys.executable)
    target_python  = os.path.abspath(VENV_PYTHON)
    if current_python != target_python:
        os.execv(target_python, [target_python] + sys.argv)

ensure_venv()

# ------------------------------------------------------------------------------
# 2. Imports from Virtual Environment
# ------------------------------------------------------------------------------
from cocotb_tools.runner import get_runner

RTL_WS2812    = os.path.join(PROJECT_ROOT, "rtl", "ws2812_driver.v")
RTL_GAMMA     = os.path.join(PROJECT_ROOT, "rtl", "gamma_lut.v")
RTL_GAMMA_MEM = os.path.join(PROJECT_ROOT, "rtl", "gamma_lut.mem")
RTL_LED_ANIM  = os.path.join(PROJECT_ROOT, "rtl", "led_animator_controller.v")

def run_ws2812_driver_test(waves=False, clean=False):
    build_dir = os.path.join(SIM_DIR, "build_ws2812")
    if clean and os.path.exists(build_dir):
        shutil.rmtree(build_dir)

    print("\n==========================================================")
    print(" [1/2] Building & Running Cocotb: WS2812B LED Driver       ")
    print("==========================================================")
    runner = get_runner("verilator")
    runner.build(
        sources=[RTL_WS2812],
        hdl_toplevel="ws2812_driver",
        build_dir=build_dir,
        build_args=["-Wno-fatal", "-Wno-DECLFILENAME", "-Wall"],
        waves=waves
    )
    runner.test(
        hdl_toplevel="ws2812_driver",
        test_module="test_ws2812_driver",
        test_dir=SIM_DIR,
        build_dir=build_dir,
        waves=waves
    )

def run_led_animator_test(waves=False, clean=False):
    build_dir = os.path.join(SIM_DIR, "build_led_animator")
    if clean and os.path.exists(build_dir):
        shutil.rmtree(build_dir)

    print("\n==========================================================")
    print(" [2/2] Building & Running Cocotb: LED Animator DMA Ctrl    ")
    print("==========================================================")
    runner = get_runner("verilator")
    runner.build(
        sources=[RTL_WS2812, RTL_GAMMA, RTL_LED_ANIM],
        hdl_toplevel="led_animator_controller",
        build_dir=build_dir,
        build_args=["-Wno-fatal", "-Wno-DECLFILENAME", "-Wall"],
        # Absolute path so $readmemh (in gamma_lut.v) finds the table
        # regardless of the simulation binary's working directory.
        parameters={"GAMMA_LUT_FILE": f'"{RTL_GAMMA_MEM}"'},
        waves=waves
    )
    runner.test(
        hdl_toplevel="led_animator_controller",
        test_module="test_led_animator",
        test_dir=SIM_DIR,
        build_dir=build_dir,
        waves=waves
    )

def main():
    parser = argparse.ArgumentParser(description="Cocotb Simulation Runner for the WS2812B LED Animator")
    parser.add_argument("--test", choices=["all", "ws2812", "led_animator"], default="all",
                        help="Select test suite to run (default: all)")
    parser.add_argument("--waves", action="store_true",
                        help="Enable VCD waveform dumping for GTKWave / Surfer")
    parser.add_argument("--clean", action="store_true",
                        help="Clean previous build artifacts before running")
    args = parser.parse_args()

    if not shutil.which("verilator"):
        print("[ERROR] Verilator not found in PATH! Please ensure Verilator is installed.")
        sys.exit(1)

    if args.clean and args.test == "all":
        for b in ["build_ws2812", "build_led_animator"]:
            p = os.path.join(SIM_DIR, b)
            if os.path.exists(p):
                shutil.rmtree(p)

    if args.test in ["all", "ws2812"]:
        run_ws2812_driver_test(waves=args.waves, clean=args.clean)

    if args.test in ["all", "led_animator"]:
        run_led_animator_test(waves=args.waves, clean=args.clean)

    print("\n==========================================================")
    print(" >>> ALL COCOTB HARDWARE SIMULATIONS PASSED! <<<         ")
    print("==========================================================\n")

if __name__ == "__main__":
    main()

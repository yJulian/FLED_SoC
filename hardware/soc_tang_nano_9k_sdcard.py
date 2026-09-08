#!/usr/bin/env python3

import os
import sys

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migen import *
from litex.gen import *
from litex.soc.integration.soc import KILOBYTE
from litex.soc.integration.builder import Builder
from litex.build.parser import LiteXArgumentParser

from litex_boards.targets.sipeed_tang_nano_9k import BaseSoC as TangNano9KBaseSoC
from litex_boards.platforms import sipeed_tang_nano_9k

class TangNano9KSDCardSoC(TangNano9KBaseSoC):
    """
    Minimal feasibility-test SoC for the Sipeed Tang Nano 9K (Gowin GW1NR-9C).

    Goal: confirm that a small RISC-V core plus the SPI-mode SDCard slot on
    this board are enough to mount a FAT filesystem and list the files in
    its root directory over the UART console.

    NOTE: Ibex (used successfully on the bigger Tang Nano 20K/GW2AR-18 in
    this project) does NOT fit here: real Gowin synthesis hit 18571 DFFs
    against this device's 6693 DFF budget (~2.8x over). VexRiscv's "minimal"
    variant is the standard small-FPGA choice instead and fits comfortably.

    NOTE: two on-chip-memory attempts (ROM ~27.4KB auto-sized BIOS + sram 8KB
    + main_ram 32KB, then trimmed to sram 4KB + main_ram 16KB) both blew LUT
    usage way past the 8640 budget (31011, then 22557 LUTs). The GW1NR-9C's
    real embedded-BRAM budget is far smaller than these combined requests,
    so nearly all of it fell back to LUT-based distributed RAM. Regression
    from those two data points puts the fit ceiling at roughly ~14.5KB of
    *total* on-chip memory for this design -- nowhere near enough for a full
    LiteX BIOS (~27KB) plus a separate main_ram.

    Fix: skip the BIOS + serialboot two-stage flow entirely. Our firmware
    (firmware_sdcard_ls/, linked BIOS-style to execute directly from ROM,
    see its linker.ld) is passed in as `integrated_rom_init`, so it IS the
    ROM content -- no separate main_ram needed at all, and no interactive
    BIOS shell overhead. Total on-chip memory: ROM (~our firmware's size,
    auto-sized) + a small sram for .data/.bss/stack. This also avoids
    HyperRAM/PSRAM calibration and any persistent SPI-flash write -- pure
    SRAM-only JTAG load, matching how this project has always worked so far.
      - RISC-V VexRiscv CPU core (variant "minimal": RV32I, no MMU/mul/div)
      - ROM = our firmware directly (no separate BIOS, no main_ram)
      - UART Serial Console
      - SPI-mode SDCard (litex.soc.cores.spi.spi_sdcard) on the "spisdcard" pins
    """
    def __init__(self, sys_clk_freq=27e6, firmware_bin=None, **kwargs):
        kwargs["cpu_type"]    = "vexriscv"
        kwargs["cpu_variant"] = "minimal"
        kwargs.setdefault("integrated_sram_size", 4 * KILOBYTE)
        # Placeholder only, to steer BaseSoC away from its HyperRAM/PSRAM
        # main_ram path (triggered whenever integrated_main_ram_size == 0);
        # our firmware runs entirely out of rom+sram and never touches this.
        kwargs.setdefault("integrated_main_ram_size", 256)
        if firmware_bin:
            kwargs["integrated_rom_init"] = firmware_bin

        super().__init__(
            sys_clk_freq        = sys_clk_freq,
            with_led_chaser      = True,
            with_integrated_rom  = True,
            **kwargs
        )

        # SPI-mode SDCard (4 pins: clk/mosi/cs_n/miso) -> liblitesdcard + libfatfs
        # in firmware/software (CSR_SPISDCARD_BASE), used for the root-dir listing test.
        self.add_spi_sdcard()

def main():
    parser = LiteXArgumentParser(platform=sipeed_tang_nano_9k.Platform, description="Tang Nano 9K SDCard Core-Feasibility SoC")
    parser.add_target_argument("--sys-clk-freq",  default=27e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--firmware-bin",  default=None,             help="Path to a prebuilt BIOS-style firmware.bin to embed directly as ROM content (skips the LiteX BIOS).")
    args = parser.parse_args()

    argdict = parser.soc_argdict
    argdict.pop("cpu_type", None)
    argdict.pop("cpu_variant", None)

    soc = TangNano9KSDCardSoC(
        toolchain    = args.toolchain,
        sys_clk_freq = args.sys_clk_freq,
        firmware_bin = args.firmware_bin,
        **argdict
    )

    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import os
import sys

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migen import *
from litex.gen import *
from litex.soc.integration.soc import SoCRegion, KILOBYTE, MEGABYTE
from litex.soc.integration.builder import Builder
from litex.build.parser import LiteXArgumentParser
from litex.build.generic_platform import Pins, IOStandard
from litex.soc.cores.hyperbus import HyperRAM

from hardware.soc_tang_nano_9k_sdcard import TangNano9KSDCardSoC
from litex_boards.platforms import sipeed_tang_nano_9k
from hardware.led_animator_module import LedAnimator

# Free pin on the J7 expansion header (not used by spisdcard, spilcd, hdmi,
# or any other predefined resource on this platform -- see
# litex_boards/platforms/sipeed_tang_nano_9k.py's _io/_connectors lists).
WS2812_PIN = "49"

# Unused window in this SoC's address map (rom=0x0, spiflash=0x400000,
# sram=0x10000000, main_ram placeholder=0x40000000, IO region starts at
# 0x80000000) -- see the "Real synthesis results" / PSRAM section in
# README.md for how this was picked.
PSRAM_BASE = 0x50000000
PSRAM_SIZE = 4 * MEGABYTE  # first of the board's two 32Mbit PSRAM chips; see _HyperRAMPads below

class TangNano9KLedAnimSoC(TangNano9KSDCardSoC):
    """
    Sipeed Tang Nano 9K SoC combining the SDCard root-listing feasibility
    config with the autonomous WS2812B LED Animator DMA core.

    Firmware reads a .fled animation file's header + raw RGB payload from
    the SDCard. Small animations still fit the on-chip SRAM buffer, but with
    `with_psram=True` (the default) the file is instead loaded into a 4MB
    PSRAM-backed "psram" bus region (see PSRAM_BASE/PSRAM_SIZE above), so
    much larger/longer animations fit too -- see firmware_led_anim/main.c.
    The CPU only needs to load the file into RAM and write the LedAnimator
    control CSRs once; the LedAnimator core then reads frames directly out
    of PSRAM (or on-chip SRAM, if with_psram=False) via its own Wishbone
    master and streams them out to a WS2812B strip on J7 pin 49 at the
    file's declared frame rate, entirely autonomously (hardware DMA +
    frame-rate pacing, no CPU polling needed once started).

    CAVEAT: the PSRAM path has been verified in `builder.build()` synthesis
    (fits, no elaboration/pin errors) but NOT yet on real hardware -- unlike
    the rest of this SoC, no board with PSRAM populated has been used to
    confirm a file actually round-trips through it correctly. The upstream
    LiteX HyperRAM core also carries a known-issues history (see the FIXME
    in litex_boards/targets/sipeed_tang_nano_9k.py). Treat `with_psram=True`
    as experimental until confirmed on real hardware; pass
    `with_psram=False` to fall back to the always-verified on-chip-SRAM-only
    path.
    """
    def __init__(self, sys_clk_freq=27e6, firmware_bin=None, with_psram=True, **kwargs):
        # The 2KB .fled payload buffer + FatFs state need more stack headroom
        # than the plain SDCard-listing config's 4KB sram; bump it here.
        kwargs.setdefault("integrated_sram_size", 8 * KILOBYTE)
        super().__init__(sys_clk_freq=sys_clk_freq, firmware_bin=firmware_bin, **kwargs)

        self.platform.add_extension([
            ("ws2812_led", 0, Pins(WS2812_PIN), IOStandard("LVCMOS33")),
        ])
        led_pad = self.platform.request("ws2812_led")

        self.led_animator = LedAnimator(self.platform, led_pad, sys_clk_freq)
        self.bus.add_master(name="led_animator", master=self.led_animator.bus)
        self.irq.add("led_animator")

        if with_psram:
            self.add_psram()

    def add_psram(self):
        # PSRAM (HyperRAM protocol) -- large external frame buffer so
        # animations are no longer limited to the on-chip SRAM's 2KB.
        # Mirrors litex_boards/targets/sipeed_tang_nano_9k.py's own
        # HyperRAM setup (only the first of the board's two 32Mbit PSRAM
        # chips is wired up; the second is unused, same as upstream).
        dq      = self.platform.request("IO_psram_dq")
        rwds    = self.platform.request("IO_psram_rwds")
        reset_n = self.platform.request("O_psram_reset_n")
        cs_n    = self.platform.request("O_psram_cs_n")
        ck      = self.platform.request("O_psram_ck")
        ck_n    = self.platform.request("O_psram_ck_n")

        class _HyperRAMPads:
            def __init__(self, n):
                self.clk   = Signal()
                self.rst_n = reset_n[n]
                self.dq    = dq[8 * n:8 * (n + 1)]
                self.cs_n  = cs_n[n]
                self.rwds  = rwds[n]

        hyperram_pads = _HyperRAMPads(0)
        self.comb += ck[0].eq(hyperram_pads.clk)
        self.comb += ck_n[0].eq(~hyperram_pads.clk)

        self.hyperram = HyperRAM(hyperram_pads)
        self.mem_map["psram"] = PSRAM_BASE
        self.bus.add_slave(name="psram", slave=self.hyperram.bus, region=SoCRegion(
            origin = PSRAM_BASE,
            size   = PSRAM_SIZE,
            mode   = "rwx",
        ))

def main():
    parser = LiteXArgumentParser(platform=sipeed_tang_nano_9k.Platform, description="Tang Nano 9K SDCard + WS2812B LED Animator SoC")
    parser.add_target_argument("--sys-clk-freq", default=27e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--firmware-bin", default=None,             help="Path to a prebuilt BIOS-style firmware.bin to embed directly as ROM content (skips the LiteX BIOS).")
    parser.add_target_argument("--no-psram", action="store_true",          help="Disable the PSRAM frame buffer (experimental); fall back to on-chip-SRAM-only, the always-verified path.")
    args = parser.parse_args()

    argdict = parser.soc_argdict
    argdict.pop("cpu_type", None)
    argdict.pop("cpu_variant", None)

    soc = TangNano9KLedAnimSoC(
        toolchain    = args.toolchain,
        sys_clk_freq = args.sys_clk_freq,
        firmware_bin = args.firmware_bin,
        with_psram   = not args.no_psram,
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

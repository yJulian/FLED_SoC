#!/usr/bin/env python3

import os
import sys

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migen import *
from litex.gen import *
from litex.soc.integration.soc import SoCRegion, KILOBYTE
from litex.soc.integration.builder import Builder
from litex.build.parser import LiteXArgumentParser
from litex.build.generic_platform import Pins, IOStandard

from hardware.soc_tang_nano_9k_sdcard import TangNano9KSDCardSoC
from litex_boards.platforms import sipeed_tang_nano_9k
from hardware.led_animator_module import LedAnimator

# Free pin on the J7 expansion header (not used by spisdcard, spilcd, hdmi,
# or any other predefined resource on this platform -- see
# litex_boards/platforms/sipeed_tang_nano_9k.py's _io/_connectors lists).
WS2812_PIN = "49"

class TangNano9KLedAnimSoC(TangNano9KSDCardSoC):
    """
    Sipeed Tang Nano 9K SoC combining the SDCard root-listing feasibility
    config with the autonomous WS2812B LED Animator DMA core.

    Firmware reads a .fled animation file's header + raw RGB payload from
    the SDCard into a small on-chip SRAM buffer (a typical simple animation
    is a few hundred bytes to a few KB -- e.g. the ANIM.FLED test file on
    the dev SD card is only 400 bytes total), then hands the buffer address
    to the LedAnimator core, which streams it out to a WS2812B strip on
    J7 pin 49 at the file's declared frame rate, entirely autonomously
    (Wishbone DMA + hardware frame-rate pacing, no CPU polling needed once
    started).
    """
    def __init__(self, sys_clk_freq=27e6, firmware_bin=None, **kwargs):
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

def main():
    parser = LiteXArgumentParser(platform=sipeed_tang_nano_9k.Platform, description="Tang Nano 9K SDCard + WS2812B LED Animator SoC")
    parser.add_target_argument("--sys-clk-freq", default=27e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--firmware-bin", default=None,             help="Path to a prebuilt BIOS-style firmware.bin to embed directly as ROM content (skips the LiteX BIOS).")
    args = parser.parse_args()

    argdict = parser.soc_argdict
    argdict.pop("cpu_type", None)
    argdict.pop("cpu_variant", None)

    soc = TangNano9KLedAnimSoC(
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

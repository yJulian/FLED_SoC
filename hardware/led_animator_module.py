import os
from migen import *
from litex.gen import *
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import *
from litex.soc.interconnect.csr_eventmanager import *

class LedAnimator(LiteXModule):
    """
    Autonomous WS2812B LED Animation DMA Player.

    Streams a pre-loaded .fled animation (raw RGB frame data, one frame
    after another, starting at `base_addr` in the SoC's memory map) out to
    a WS2812B LED strip at a fixed frame rate, entirely in hardware once
    started -- the CPU only needs to load the file into RAM and write the
    control CSRs once.

    Operates as a Wishbone Bus Master for memory reads and features an
    EventManager for an optional per-frame completion interrupt.
    """
    def __init__(self, platform, pads, sys_clk_freq):
        # 32-bit Wishbone Bus Master Interface
        self.bus = wishbone.Interface(data_width=32, adr_width=30)

        # Control & Configuration Registers (CSRs)
        self.base_addr      = CSRStorage(32, description="Byte address of frame 0, pixel 0 (the .fled payload after its 16-byte header)")
        self.led_count      = CSRStorage(16, description="Number of LEDs per frame (.fled header field)")
        self.frame_count    = CSRStorage(32, description="Total number of frames (.fled header field)")
        self.frame_interval = CSRStorage(32, description="sys_clk cycles between frame starts (sys_clk_freq / target FPS)")
        self.enable          = CSRStorage(1,  description="Write 1 to (re)start playback from frame 0; write 0 to stop at the next frame boundary")
        self.loop             = CSRStorage(1,  description="1 = wrap back to frame 0 after the last frame")

        # Status Registers
        self.busy          = CSRStatus(1,  description="1 while an animation is playing")
        self.current_frame = CSRStatus(32, description="Index of the frame currently being (or about to be) displayed")

        # Hardware Interrupt Event Manager (pulses once per displayed frame)
        self.ev            = EventManager()
        self.ev.frame_done = EventSourcePulse(description="One LED frame has finished being sent to the strip")
        self.ev.finalize()

        irq_frame_done  = Signal()
        cfg_start_pulse = Signal()
        self.comb += [
            self.ev.frame_done.trigger.eq(irq_frame_done),
            # Write-strobe (not level): a plain write of 1 to `enable` (re)starts
            # playback from frame 0. Stopping (write 0) is handled as a level by
            # the RTL directly on self.enable.storage[0], so a non-looping
            # animation left with enable=1 after finishing does not self-restart.
            cfg_start_pulse.eq(self.enable.re & self.enable.storage[0]),
        ]

        # Add RTL Verilog sources
        rtl_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rtl"))
        platform.add_source(os.path.join(rtl_dir, "ws2812_driver.v"))
        platform.add_source(os.path.join(rtl_dir, "led_animator_controller.v"))

        self.specials += Instance("led_animator_controller",
            p_SYS_CLK_FREQ_HZ = int(sys_clk_freq),

            i_clk   = ClockSignal("sys"),
            i_rst_n = ~ResetSignal("sys"),

            # Wishbone Master Interface
            o_m_wb_adr   = self.bus.adr,
            o_m_wb_dat_w = self.bus.dat_w,
            i_m_wb_dat_r = self.bus.dat_r,
            o_m_wb_sel   = self.bus.sel,
            o_m_wb_cyc   = self.bus.cyc,
            o_m_wb_stb   = self.bus.stb,
            o_m_wb_we    = self.bus.we,
            i_m_wb_ack   = self.bus.ack,

            # Configuration Inputs
            i_cfg_base_addr      = self.base_addr.storage,
            i_cfg_led_count      = self.led_count.storage,
            i_cfg_frame_count    = self.frame_count.storage,
            i_cfg_frame_interval = self.frame_interval.storage,
            i_cfg_enable          = self.enable.storage[0],
            i_cfg_start_pulse     = cfg_start_pulse,
            i_cfg_loop             = self.loop.storage[0],

            # Status Outputs
            o_status_busy          = self.busy.status,
            o_status_current_frame = self.current_frame.status,
            o_irq_frame_done       = irq_frame_done,

            # Physical LED strip output
            o_led_dout = pads,
        )

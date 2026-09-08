"""
Cocotb testbench for the Autonomous LED Animator DMA Controller
(rtl/led_animator_controller.v, which internally instantiates
rtl/ws2812_driver.v).

Uses a mock Wishbone-slave memory (same style as sim/test_matrix_dma.py) to
stand in for the .fled animation payload in RAM. Verifies:
 1. The byte-stream reader correctly assembles pixels from memory even when
    the animation's base address is NOT word-aligned (frames of 3 LEDs *
    3 bytes = 9 bytes don't naturally land on 4-byte boundaries either)
 2. Non-looping playback stops after the last configured frame
 3. Looping playback wraps back to frame 0 and keeps going
 4. One irq_frame_done pulse per displayed frame, and status_current_frame
    tracking
 5. The very first pixel of frame 0 is correctly reproduced on the physical
    `led_dout` line (RGB -> GRB reordering + correct source byte offsets)
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Timer

SYS_CLK_FREQ_HZ = 27_000_000
CLK_PERIOD_PS = round(1_000_000_000_000 / SYS_CLK_FREQ_HZ / 2) * 2  # must be even (period_high defaults to half)

def cycles(us):
    return ((SYS_CLK_FREQ_HZ // 1000) * int(us * 1000) + 500_000) // 1_000_000

T0H, T0L, T1H, T1L = cycles(0.4), cycles(0.85), cycles(0.8), cycles(0.45)

async def wishbone_slave_responder(dut, mem):
    """Simulates a byte-addressable Wishbone slave memory, word (4-byte) granularity."""
    dut.m_wb_ack.value = 0
    dut.m_wb_dat_r.value = 0
    while True:
        await RisingEdge(dut.clk)
        if int(dut.rst_n.value) == 0:
            dut.m_wb_ack.value = 0
            dut.m_wb_dat_r.value = 0
            continue

        if int(dut.m_wb_cyc.value) == 1 and int(dut.m_wb_stb.value) == 1 and int(dut.m_wb_ack.value) == 0:
            word_addr = int(dut.m_wb_adr.value) & 0xFFFFF
            dut.m_wb_dat_r.value = mem.get(word_addr, 0)
            dut.m_wb_ack.value = 1
        else:
            dut.m_wb_ack.value = 0

def store_bytes(mem, byte_base, data: bytes):
    """Stores a byte string into the word-addressed mock memory, little-endian."""
    for i, byte_val in enumerate(data):
        byte_addr = byte_base + i
        word_addr = byte_addr // 4
        shift = (byte_addr % 4) * 8
        word = mem.get(word_addr, 0)
        word &= ~(0xFF << shift)
        word |= (byte_val << shift)
        mem[word_addr] = word

async def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_PS, unit="ps").start())

async def trigger_start(dut):
    """Sets the enable level and pulses cfg_start_pulse for one cycle (mirrors
    the CSR write-strobe the Migen wrapper derives from a plain `enable` write)."""
    dut.cfg_enable.value = 1
    dut.cfg_start_pulse.value = 1
    await RisingEdge(dut.clk)
    dut.cfg_start_pulse.value = 0

async def reset_dut(dut, mem):
    cocotb.start_soon(wishbone_slave_responder(dut, mem))
    dut.rst_n.value = 0
    dut.cfg_base_addr.value = 0
    dut.cfg_led_count.value = 0
    dut.cfg_frame_count.value = 0
    dut.cfg_frame_interval.value = 0
    dut.cfg_enable.value = 0
    dut.cfg_start_pulse.value = 0
    dut.cfg_loop.value = 0
    await Timer(5 * CLK_PERIOD_PS, unit="ps")
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

async def sample_dout_timeline(dut, num_cycles):
    timeline = []
    for _ in range(num_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        timeline.append(int(dut.dout.value) if hasattr(dut, "dout") else int(dut.led_dout.value))
    return timeline

def run_lengths(timeline):
    runs = []
    cur, n = timeline[0], 1
    for v in timeline[1:]:
        if v == cur:
            n += 1
        else:
            runs.append((cur, n))
            cur, n = v, 1
    runs.append((cur, n))
    return runs

def decode_first_pixel_grb(timeline):
    runs = run_lengths(timeline)
    if runs[0][0] == 0:
        runs = runs[1:]  # drop the pre-transmission idle-low run
    # Bit 23 (the very last bit of the pixel) may have its LOW phase merged
    # with a following bit or the reset/latch pulse (both are just
    # "dout=0", no edge between them) -- decode it from its HIGH duration
    # alone, same as sim/test_ws2812_driver.py.
    bits = ""
    for i in range(24):
        high_val, high_len = runs[2 * i]
        low_val = runs[2 * i + 1][0]
        assert high_val == 1 and low_val == 0
        if i < 23:
            low_len = runs[2 * i + 1][1]
            if high_len == T1H and low_len == T1L:
                bits += "1"
            elif high_len == T0H and low_len == T0L:
                bits += "0"
            else:
                raise AssertionError(f"bit {i}: HIGH={high_len} LOW={low_len} unrecognized")
        else:
            if high_len == T1H:
                bits += "1"
            elif high_len == T0H:
                bits += "0"
            else:
                raise AssertionError(f"bit {i}: HIGH={high_len} matches neither T0H={T0H} nor T1H={T1H}")
    g, r, b = int(bits[0:8], 2), int(bits[8:16], 2), int(bits[16:24], 2)
    return r, g, b

LED_COUNT = 3
BYTES_PER_FRAME = LED_COUNT * 3

def make_frame_bytes(frame_idx, led_count=LED_COUNT):
    """Deterministic, distinct RGB bytes per (frame, led)."""
    out = bytearray()
    for led in range(led_count):
        out += bytes([
            (0x10 * (frame_idx + 1) + led) & 0xFF,
            (0x20 * (frame_idx + 1) + led) & 0xFF,
            (0x30 * (frame_idx + 1) + led) & 0xFF,
        ])
    return bytes(out)

@cocotb.test()
async def test_first_pixel_unaligned_base_addr(dut):
    """Base address deliberately not word-aligned; verify first pixel decodes correctly."""
    mem = {}
    await start_clock(dut)
    await reset_dut(dut, mem)

    base_addr = 0x11  # not a multiple of 4
    frame0 = make_frame_bytes(0)
    store_bytes(mem, base_addr, frame0)

    dut.cfg_base_addr.value = base_addr
    dut.cfg_led_count.value = LED_COUNT
    dut.cfg_frame_count.value = 1
    dut.cfg_frame_interval.value = 50_000  # long enough we won't see a 2nd frame during capture
    dut.cfg_loop.value = 0

    await trigger_start(dut)

    budget = LED_COUNT * 24 * (T0H + T0L) + 300
    timeline = await sample_dout_timeline(dut, budget)

    r, g, b = decode_first_pixel_grb(timeline)
    exp_r, exp_g, exp_b = frame0[0], frame0[1], frame0[2]
    dut._log.info(f"Decoded first pixel: R={r:#04x} G={g:#04x} B={b:#04x}")
    dut._log.info(f"Expected first pixel: R={exp_r:#04x} G={exp_g:#04x} B={exp_b:#04x}")
    assert (r, g, b) == (exp_r, exp_g, exp_b), "first pixel mismatch (unaligned base addr byte-stream bug?)"
    dut._log.info("PASS: first pixel correctly reproduced from a non-word-aligned base address")

@cocotb.test()
async def test_non_looping_stops_after_last_frame(dut):
    """3 frames, no loop: busy should deassert and exactly 3 frame_done pulses fire."""
    mem = {}
    await start_clock(dut)
    await reset_dut(dut, mem)

    base_addr = 0x100
    num_frames = 3
    for f in range(num_frames):
        store_bytes(mem, base_addr + f * BYTES_PER_FRAME, make_frame_bytes(f))

    dut.cfg_base_addr.value = base_addr
    dut.cfg_led_count.value = LED_COUNT
    dut.cfg_frame_count.value = num_frames
    dut.cfg_frame_interval.value = 200  # short interval, faster test
    dut.cfg_loop.value = 0

    await trigger_start(dut)

    frame_done_count = 0
    max_frame_seen = -1
    budget_cycles = num_frames * (LED_COUNT * 24 * (T1H + T1L) + cycles_reset() + 2000) * 2
    for _ in range(budget_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.irq_frame_done.value) == 1:
            frame_done_count += 1
            max_frame_seen = max(max_frame_seen, int(dut.status_current_frame.value))
        if frame_done_count >= num_frames and int(dut.status_busy.value) == 0:
            break

    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.status_busy.value) == 0, "expected playback to stop after the last frame"
    assert frame_done_count == num_frames, f"expected {num_frames} frame_done pulses, got {frame_done_count}"
    dut._log.info(f"PASS: non-looping playback fired {frame_done_count} frame_done pulses then stopped")

@cocotb.test()
async def test_looping_wraps_to_frame_zero(dut):
    """2 frames, loop enabled: expect frame index sequence 0,1,0,1,... for several cycles."""
    mem = {}
    await start_clock(dut)
    await reset_dut(dut, mem)

    base_addr = 0x200
    num_frames = 2
    for f in range(num_frames):
        store_bytes(mem, base_addr + f * BYTES_PER_FRAME, make_frame_bytes(f))

    dut.cfg_base_addr.value = base_addr
    dut.cfg_led_count.value = LED_COUNT
    dut.cfg_frame_count.value = num_frames
    dut.cfg_frame_interval.value = 200
    dut.cfg_loop.value = 1

    await trigger_start(dut)

    observed_frames = []
    budget_cycles = 6 * (LED_COUNT * 24 * (T1H + T1L) + cycles_reset() + 2000) * 2
    for _ in range(budget_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.irq_frame_done.value) == 1:
            observed_frames.append(int(dut.status_current_frame.value))
        if len(observed_frames) >= 5:
            break

    await RisingEdge(dut.clk)  # leave the ReadOnly phase from the loop above before writing again
    dut.cfg_enable.value = 0  # stop the animation cleanly

    dut._log.info(f"Observed frame index sequence: {observed_frames}")
    assert len(observed_frames) >= 5, "did not observe enough looped frames"
    for i, f in enumerate(observed_frames):
        assert f == (i % num_frames), f"loop sequence mismatch at step {i}: got frame {f}"
    dut._log.info("PASS: looping playback wraps back to frame 0 correctly")

def cycles_reset():
    return (SYS_CLK_FREQ_HZ // 1_000_000) * 300

"""
Cocotb testbench for the WS2812B one-wire LED driver (rtl/ws2812_driver.v).

Verifies, at the default SYS_CLK_FREQ_HZ=27_000_000 (matching the Tang Nano
9K's system clock):
 1. Exact per-phase cycle counts for '0' and '1' bits (T0H/T0L/T1H/T1L)
 2. RGB (caller order) -> GRB (physical wire order) byte reordering
 3. Multi-pixel streaming via the pixel_valid/pixel_ready handshake
 4. The low reset/latch pulse (>= RESET_US) appended after the last pixel
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Timer

SYS_CLK_FREQ_HZ = 27_000_000
CLK_PERIOD_PS = round(1_000_000_000_000 / SYS_CLK_FREQ_HZ / 2) * 2  # must be even (period_high defaults to half)

# Expected cycle counts, computed with the exact same rounding formula as the RTL.
def cycles(us):
    return ((SYS_CLK_FREQ_HZ // 1000) * int(us * 1000) + 500_000) // 1_000_000

T0H = cycles(0.4)
T0L = cycles(0.85)
T1H = cycles(0.8)
T1L = cycles(0.45)
RESET_CYCLES = (SYS_CLK_FREQ_HZ // 1_000_000) * 300  # RESET_US default = 300

async def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_PS, unit="ps").start())

async def reset_dut(dut):
    dut.rst_n.value = 0
    dut.pixel_data.value = 0
    dut.pixel_valid.value = 0
    dut.num_leds.value = 0
    dut.start.value = 0
    await Timer(5 * CLK_PERIOD_PS, unit="ps")
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

async def pixel_feeder(dut, pixels):
    """Drives pixel_data/pixel_valid, advancing on each pixel_ready pulse."""
    idx = 0
    dut.pixel_data.value = pixels[0]
    dut.pixel_valid.value = 1
    while idx < len(pixels):
        await RisingEdge(dut.clk)
        if int(dut.pixel_ready.value) == 1:
            idx += 1
            if idx < len(pixels):
                dut.pixel_data.value = pixels[idx]
            else:
                dut.pixel_valid.value = 0
    dut.pixel_valid.value = 0

async def sample_dout_timeline(dut, num_cycles):
    """Samples dout at every rising clock edge, returns list of ints."""
    timeline = []
    for _ in range(num_cycles):
        await RisingEdge(dut.clk)
        timeline.append(int(dut.dout.value))
    return timeline

def run_lengths(timeline):
    """Run-length encode a 0/1 timeline into [(value, length), ...]."""
    runs = []
    cur = timeline[0]
    n = 1
    for v in timeline[1:]:
        if v == cur:
            n += 1
        else:
            runs.append((cur, n))
            cur, n = v, 1
    runs.append((cur, n))
    return runs

@cocotb.test()
async def test_single_pixel_bit_timing_and_reorder(dut):
    """Send one RGB pixel, verify exact bit timings and GRB reordering."""
    await start_clock(dut)
    await reset_dut(dut)

    r, g, b = 0b10110010, 0b01001101, 0b11100001
    pixel_rgb = (r << 16) | (g << 8) | b
    expected_wire_bits = f"{g:08b}{r:08b}{b:08b}"  # GRB order, MSB first

    dut.num_leds.value = 1
    feeder = cocotb.start_soon(pixel_feeder(dut, [pixel_rgb]))

    await RisingEdge(dut.clk)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    # 24 bits * (T?H+T?L) worst-case, plus reset, plus margin.
    total_cycles = 24 * (T1H + T1L) + RESET_CYCLES + 50
    timeline = await sample_dout_timeline(dut, total_cycles)
    await feeder

    runs = run_lengths(timeline)
    if runs[0][0] == 0:
        runs = runs[1:]  # drop the pre-transmission idle-low run
    dut._log.info(f"Captured {len(runs)} runs from dout timeline")

    # Decode 24 bits from 48 runs (HIGH,LOW pairs). The very last bit's LOW
    # phase has no rising edge before the reset/latch pulse (both are just
    # "dout=0"), so it merges into one long run with the reset pulse -- that
    # merge is correct, expected hardware behavior, not a bug. Decode the
    # last bit from its HIGH duration alone, and check the merged low+reset
    # run is at least as long as that bit's LOW phase plus the reset pulse.
    decoded_bits = ""
    for i in range(24):
        high_val, high_len = runs[2 * i]
        low_val, low_len = runs[2 * i + 1]
        assert high_val == 1, f"bit {i}: expected HIGH phase first, run={runs[2*i]}"
        assert low_val == 0, f"bit {i}: expected LOW phase second, run={runs[2*i+1]}"

        if i < 23:
            if high_len == T1H and low_len == T1L:
                decoded_bits += "1"
            elif high_len == T0H and low_len == T0L:
                decoded_bits += "0"
            else:
                raise AssertionError(
                    f"bit {i}: HIGH={high_len} LOW={low_len} matches neither "
                    f"'0'(T0H={T0H},T0L={T0L}) nor '1'(T1H={T1H},T1L={T1L})"
                )
        else:
            if high_len == T1H:
                decoded_bits += "1"
                expected_low = T1L
            elif high_len == T0H:
                decoded_bits += "0"
                expected_low = T0L
            else:
                raise AssertionError(f"bit {i}: HIGH={high_len} matches neither T0H={T0H} nor T1H={T1H}")
            assert low_len >= expected_low + RESET_CYCLES, (
                f"bit {i}: merged low+reset run too short: {low_len} < {expected_low}+{RESET_CYCLES}"
            )
            dut._log.info(f"Reset/latch pulse (merged with last bit's LOW): {low_len} cycles [PASS]")

    dut._log.info(f"Decoded wire bits : {decoded_bits}")
    dut._log.info(f"Expected wire bits: {expected_wire_bits}")
    assert decoded_bits == expected_wire_bits, "GRB wire-order bit pattern mismatch"

@cocotb.test()
async def test_multi_pixel_stream_and_done_pulse(dut):
    """Stream 3 pixels back-to-back and check busy/done handshaking."""
    await start_clock(dut)
    await reset_dut(dut)

    pixels = [0xFF0000, 0x00FF00, 0x0000FF]  # pure R, G, B (caller RGB order)
    dut.num_leds.value = len(pixels)
    feeder = cocotb.start_soon(pixel_feeder(dut, pixels))

    await RisingEdge(dut.clk)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    await ReadOnly()
    assert int(dut.busy.value) == 1, "busy should assert right after start"

    # Wait for done, with a generous cycle budget.
    budget = 3 * 24 * (T0H + T0L) + RESET_CYCLES + 200
    done_seen = False
    for _ in range(budget):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.done.value) == 1:
            done_seen = True
            break
    await feeder

    assert done_seen, "done pulse was never asserted"
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.busy.value) == 0, "busy should deassert once done"
    dut._log.info("PASS: 3-pixel stream completed with correct busy/done handshake")

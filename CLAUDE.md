# Design history: rejected approaches

This file collects the "we tried X, it didn't fit/work, so we did Y instead"
history that used to live inline in README.md and code comments. It's kept
here instead so the README and source comments describe only the current
design, not the dead ends along the way. Read this when touching CPU choice,
on-chip memory sizing, or the boot flow — the reasoning below is why those
areas look the way they do.

## CPU core: VexRiscv "minimal", not Ibex

Ibex was tried first (it's used successfully on the bigger Tang Nano 20K /
GW2AR-18 in the sibling project this repo was split out of). It does not fit
the Tang Nano 9K's Gowin GW1NR-9C: real Gowin synthesis hit **18571 DFFs**
against this device's **6693 DFF budget** (~2.8x over).

VexRiscv's `minimal` variant (RV32I, no MMU/mul/div) fits comfortably instead
and is now the CPU used by both SoC targets — see
`hardware/soc_tang_nano_9k_sdcard.py`'s `kwargs["cpu_type"]`/`cpu_variant`
assignment (inherited by `hardware/soc_tang_nano_9k_led_anim.py`).

## Boot flow: no BIOS/serialboot, not a full BIOS + main_ram

Two on-chip-memory configurations were tried before landing on the current
ROM-only boot flow:

1. ROM ~27.4KB (auto-sized LiteX BIOS) + sram 8KB + main_ram 32KB →
   **31011 LUTs** used.
2. Trimmed to sram 4KB + main_ram 16KB → **22557 LUTs** used.

Both blew past the GW1NR-9C's 8640 LUT budget. The GW1NR-9C's real
embedded-BRAM budget is far smaller than either combined request, so nearly
all of it fell back to LUT-based distributed RAM (~1 LUT per few bits).
Regression from those two data points puts the fit ceiling at roughly
**~14.5KB of total on-chip memory** for this design — nowhere near enough
for a full LiteX BIOS (~27KB) plus a separate main_ram.

Fix, now the standing design: skip the BIOS + serialboot two-stage flow
entirely. Firmware is linked BIOS-style (executes directly from ROM, see
`firmware_*/linker.ld`) and passed to LiteX as `integrated_rom_init`, so it
*is* the ROM content — no separate main_ram needed, no interactive BIOS
shell overhead. Total on-chip memory is just ROM (firmware size, auto-sized)
+ a small sram for `.data`/`.bss`/stack. See `hardware/soc_tang_nano_9k_sdcard.py`
and `build_and_run.py` for how the two-phase build (generate headers, compile
firmware, regenerate with `--firmware-bin`) implements this.

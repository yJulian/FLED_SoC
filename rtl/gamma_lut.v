/*
 * 8-bit Gamma-Correction Lookup Table (gamma = 2.8)
 *
 * WS2812B strips are driven with linear PWM duty cycles, but human
 * brightness perception is roughly logarithmic -- fed raw linear RGB
 * bytes, low-brightness colors look washed out/greyish and most of the
 * visual range gets crammed into the top of the byte range. This table
 * remaps each linear input byte to a perceptually-corrected output byte
 * (out = round(255 * (in/255)^2.8), gamma=2.8 matches the common WS2812
 * default e.g. used by FastLED), synthesized as a small ROM (single-cycle
 * combinational lookup, no pipeline latency added to the byte-stream
 * reader in led_animator_controller.v).
 *
 * Table contents live in gamma_lut.mem (256 lines, one 2-hex-digit byte
 * each, address = line number = the input byte), generated with:
 * round(255 * (i/255)**2.8) for i in 0..255. GAMMA_LUT_FILE defaults to
 * the bare filename (resolved relative to the simulator/synthesizer's
 * working directory) -- callers that can't guarantee gamma_lut.mem is
 * there (e.g. led_animator_controller.v, and hardware/led_animator_module.py
 * one level up from that) should override it with an absolute path.
 */
`timescale 1ns / 1ps

module gamma_lut #(
    parameter GAMMA_LUT_FILE = "gamma_lut.mem"
) (
    input  wire [7:0] in_byte,
    output wire [7:0] out_byte
);

    reg [7:0] rom [0:255];

    initial $readmemh(GAMMA_LUT_FILE, rom);

    assign out_byte = rom[in_byte];

endmodule

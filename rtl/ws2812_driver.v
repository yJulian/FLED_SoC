/*
 * WS2812B ("NeoPixel") Single-Wire LED Driver
 *
 * Bit-bangs the WS2812B one-wire NRZ protocol. Streams `num_leds` 24-bit
 * pixels (consumed one at a time via a simple valid/ready handshake, RGB
 * byte order as supplied by the caller -- this module reorders to the
 * physical GRB wire order internally) and automatically appends the
 * required low-level reset/latch pulse after the last pixel.
 *
 * Bit timing (target, per WS2812B datasheet):
 *   '0' bit: ~0.4us HIGH, ~0.85us LOW  (T0H / T0L)
 *   '1' bit: ~0.8us HIGH, ~0.45us LOW  (T1H / T1L)
 *   Reset/latch: >= RESET_US low (default 300us -- comfortably covers both
 *   classic WS2812B and its more reset-hungry clones)
 *
 * SYS_CLK_FREQ_HZ must match the actual system clock driving this module
 * so the cycle counts below land close to the target microsecond timings.
 * Note: if `pixel_valid` stalls for longer than the LED's reset threshold
 * (tens of us) between pixels, the strip will latch a partial frame early;
 * keep the upstream memory feed fast enough to avoid that (not a concern
 * for on-chip/PSRAM-speed reads).
 */
`timescale 1ns / 1ps

module ws2812_driver #(
    parameter SYS_CLK_FREQ_HZ = 27_000_000,
    parameter RESET_US        = 300
) (
    input  wire        clk,
    input  wire         rst_n,

    // Pixel stream input (RGB byte order: {R[7:0], G[7:0], B[7:0]})
    input  wire [23:0] pixel_data,
    input  wire         pixel_valid,
    output reg          pixel_ready,  // one-cycle pulse: pixel_data consumed

    input  wire [15:0] num_leds,
    input  wire         start,        // pulse: begin streaming num_leds pixels
    output reg          busy,
    output reg          done,         // one-cycle pulse: frame (incl. reset code) complete

    output reg          dout
);

    // ---- Cycle counts for the timing segments (rounded to nearest cycle) ----
    localparam integer T0H_CYCLES   = ((SYS_CLK_FREQ_HZ / 1000) * 400 + 500_000) / 1_000_000;
    localparam integer T0L_CYCLES   = ((SYS_CLK_FREQ_HZ / 1000) * 850 + 500_000) / 1_000_000;
    localparam integer T1H_CYCLES   = ((SYS_CLK_FREQ_HZ / 1000) * 800 + 500_000) / 1_000_000;
    localparam integer T1L_CYCLES   = ((SYS_CLK_FREQ_HZ / 1000) * 450 + 500_000) / 1_000_000;
    localparam integer RESET_CYCLES = (SYS_CLK_FREQ_HZ / 1_000_000) * RESET_US;

    localparam ST_IDLE     = 3'd0;
    localparam ST_LOAD_PIX = 3'd1;
    localparam ST_BIT_HIGH = 3'd2;
    localparam ST_BIT_LOW  = 3'd3;
    localparam ST_RESET    = 3'd4;
    localparam ST_DONE     = 3'd5;

    reg [2:0]  state;
    reg [23:0] shift_reg;   // wire order after reorder: {G[7:0], R[7:0], B[7:0]}
    reg [4:0]  bit_idx;     // 23 downto 0, MSB-first per pixel
    reg [15:0] led_idx;
    reg [31:0] cyc_cnt;

    wire cur_bit = shift_reg[bit_idx];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= ST_IDLE;
            pixel_ready <= 1'b0;
            busy        <= 1'b0;
            done        <= 1'b0;
            dout        <= 1'b0;
            bit_idx     <= 5'd0;
            led_idx     <= 16'd0;
            cyc_cnt     <= 32'd0;
            shift_reg   <= 24'd0;
        end else begin
            pixel_ready <= 1'b0;
            done        <= 1'b0;

            case (state)
                ST_IDLE: begin
                    dout <= 1'b0;
                    if (start && (num_leds != 16'd0)) begin
                        busy    <= 1'b1;
                        led_idx <= 16'd0;
                        state   <= ST_LOAD_PIX;
                    end
                end

                // Wait for the next pixel; reorder RGB (caller order) -> GRB (wire order)
                ST_LOAD_PIX: begin
                    if (pixel_valid) begin
                        shift_reg   <= {pixel_data[15:8], pixel_data[23:16], pixel_data[7:0]};
                        pixel_ready <= 1'b1;
                        bit_idx     <= 5'd23;
                        cyc_cnt     <= 32'd0;
                        dout        <= 1'b1;
                        state       <= ST_BIT_HIGH;
                    end
                end

                ST_BIT_HIGH: begin
                    if (cyc_cnt + 1'b1 >= (cur_bit ? T1H_CYCLES[31:0] : T0H_CYCLES[31:0])) begin
                        dout    <= 1'b0;
                        cyc_cnt <= 32'd0;
                        state   <= ST_BIT_LOW;
                    end else begin
                        cyc_cnt <= cyc_cnt + 1'b1;
                    end
                end

                ST_BIT_LOW: begin
                    if (cyc_cnt + 1'b1 >= (cur_bit ? T1L_CYCLES[31:0] : T0L_CYCLES[31:0])) begin
                        if (bit_idx == 5'd0) begin
                            // Finished this pixel's 24 bits.
                            if (led_idx == num_leds - 16'd1) begin
                                cyc_cnt <= 32'd0;
                                state   <= ST_RESET;
                            end else begin
                                led_idx <= led_idx + 16'd1;
                                state   <= ST_LOAD_PIX;
                            end
                        end else begin
                            bit_idx <= bit_idx - 5'd1;
                            cyc_cnt <= 32'd0;
                            dout    <= 1'b1;
                            state   <= ST_BIT_HIGH;
                        end
                    end else begin
                        cyc_cnt <= cyc_cnt + 1'b1;
                    end
                end

                ST_RESET: begin
                    dout <= 1'b0;
                    if (cyc_cnt + 1'b1 >= RESET_CYCLES[31:0]) begin
                        state <= ST_DONE;
                    end else begin
                        cyc_cnt <= cyc_cnt + 1'b1;
                    end
                end

                ST_DONE: begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule

/*
 * Autonomous LED Animator DMA Controller
 * Streams pre-loaded .fled animation frames from memory to a WS2812B LED
 * strip at a fixed frame rate, entirely without CPU intervention once
 * started.
 *
 * Architecture:
 *  - 32-bit Wishbone Master Bus Interface (word-addressed reads of a
 *    contiguous byte stream: frame 0 pixel 0 R,G,B, frame 0 pixel 1 R,G,B,
 *    ..., frame 1 pixel 0 R,G,B, ... -- exactly the raw RGB payload of a
 *    .fled file, starting at cfg_base_addr)
 *  - Byte-stream reader that pulls one 32-bit word at a time and hands out
 *    individual bytes as needed, so led_count*3 need not be a multiple of 4
 *  - Frame-rate pacing timer (start-to-start, in sys_clk cycles) so the
 *    displayed frame rate stays constant regardless of how quickly a frame
 *    is actually transmitted
 *  - Optional looping playback (wraps back to frame 0 after the last frame)
 *  - Optional gamma correction (gamma_lut.v), applied per R/G/B byte before
 *    handoff to the WS2812 driver, toggled by cfg_gamma_enable
 *  - Hardware completion interrupt pulse, once per displayed frame
 *  - Drives an internal ws2812_driver instance which performs the actual
 *    one-wire protocol bit-banging
 *
 * No multiply is used on the per-frame hot path: since frames are stored
 * back-to-back, the byte address naturally lands on the next frame's start
 * once led_count*3 bytes have been streamed; only a loop-wrap resets it
 * (to cfg_base_addr, no multiply needed there either).
 */

`timescale 1ns / 1ps

module led_animator_controller #(
    parameter SYS_CLK_FREQ_HZ = 27_000_000,
    parameter GAMMA_LUT_FILE  = "gamma_lut.mem" // see rtl/gamma_lut.v -- override with an absolute path
) (
    input  wire        clk,
    input  wire        rst_n,

    // Wishbone Master Interface
    output reg  [29:0] m_wb_adr,   // 30-bit word address (byte_addr >> 2)
    output reg  [31:0] m_wb_dat_w, // unused (read-only master), tied to 0
    input  wire [31:0] m_wb_dat_r,
    output reg  [3:0]  m_wb_sel,
    output reg          m_wb_cyc,
    output reg          m_wb_stb,
    output reg          m_wb_we,
    input  wire         m_wb_ack,

    // CSR Configuration
    input  wire [31:0] cfg_base_addr,      // byte address of frame 0 pixel 0
    input  wire [15:0] cfg_led_count,
    input  wire [31:0] cfg_frame_count,
    input  wire [31:0] cfg_frame_interval, // sys_clk cycles between frame starts
    input  wire         cfg_enable,         // level: while playing, 0 stops at the next frame boundary
    input  wire         cfg_start_pulse,    // one-cycle pulse (CSR write-strobe): (re)trigger playback from frame 0
    input  wire         cfg_loop,           // 1 = wrap at frame_count, 0 = stop after last frame
    input  wire         cfg_gamma_enable,   // 1 = apply the gamma_lut correction to each R/G/B byte

    // Status
    output reg          status_busy,
    output reg  [31:0] status_current_frame,
    output reg          irq_frame_done,

    // Physical LED strip output
    output wire          led_dout
);

    // FSM State Encoding
    localparam ST_IDLE              = 3'd0;
    localparam ST_FRAME_BEGIN       = 3'd1;
    localparam ST_FETCH_BYTE        = 3'd2;
    localparam ST_WORD_WAIT         = 3'd3;
    localparam ST_PRESENT_PIXEL     = 3'd4;
    localparam ST_FRAME_WAIT_DONE   = 3'd5;
    localparam ST_FRAME_TIMING_GATE = 3'd6;
    localparam ST_ADVANCE_FRAME     = 3'd7;

    reg [2:0] state;

    // Latched configuration for the current animation run
    reg [31:0] reg_base_addr;
    reg [15:0] reg_led_count;
    reg [31:0] reg_frame_count;
    reg [31:0] reg_frame_interval;
    reg        reg_loop;

    // Sequential byte-stream reader state
    reg [31:0] byte_addr;   // absolute byte address of the next unconsumed byte
    reg [31:0] word_buf;
    reg        word_valid;
    reg [1:0]  byte_sub_idx; // 0=R, 1=G, 2=B within the pixel being gathered
    reg [7:0]  pix_r, pix_g;

    reg [15:0] px_idx;      // pixels handed to the driver so far, this frame
    reg [31:0] frame_timer; // cycles elapsed since the current frame started

    wire [7:0] cur_byte = word_buf[byte_addr[1:0]*8 +: 8];

    // ---- gamma_lut instance: single shared combinational lookup, since the
    // byte-stream reader only ever handles one R/G/B byte per cycle ----
    wire [7:0] gamma_byte;
    gamma_lut #(
        .GAMMA_LUT_FILE(GAMMA_LUT_FILE)
    ) u_gamma_lut (
        .in_byte  (cur_byte),
        .out_byte (gamma_byte)
    );
    wire [7:0] cur_byte_corrected = cfg_gamma_enable ? gamma_byte : cur_byte;

    // ---- ws2812_driver instance & handshake signals ----
    reg  [23:0] led_pixel_data;
    reg          led_pixel_valid;
    wire         led_pixel_ready;
    reg  [15:0] led_num_leds;
    reg          led_start;
    wire         led_busy;
    wire         led_done;

    ws2812_driver #(
        .SYS_CLK_FREQ_HZ(SYS_CLK_FREQ_HZ)
    ) u_ws2812_driver (
        .clk         (clk),
        .rst_n       (rst_n),
        .pixel_data  (led_pixel_data),
        .pixel_valid (led_pixel_valid),
        .pixel_ready (led_pixel_ready),
        .num_leds    (led_num_leds),
        .start       (led_start),
        .busy        (led_busy),
        .done        (led_done),
        .dout        (led_dout)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state                <= ST_IDLE;
            m_wb_adr             <= 30'd0;
            m_wb_dat_w           <= 32'd0;
            m_wb_sel             <= 4'b1111;
            m_wb_cyc             <= 1'b0;
            m_wb_stb             <= 1'b0;
            m_wb_we              <= 1'b0;
            status_busy          <= 1'b0;
            status_current_frame <= 32'd0;
            irq_frame_done       <= 1'b0;
            byte_addr            <= 32'd0;
            word_buf             <= 32'd0;
            word_valid           <= 1'b0;
            byte_sub_idx         <= 2'd0;
            pix_r                <= 8'd0;
            pix_g                <= 8'd0;
            px_idx               <= 16'd0;
            frame_timer          <= 32'd0;
            led_pixel_data       <= 24'd0;
            led_pixel_valid      <= 1'b0;
            led_num_leds         <= 16'd0;
            led_start            <= 1'b0;
        end else begin
            irq_frame_done <= 1'b0; // default pulse
            led_start      <= 1'b0; // default pulse

            if (status_busy) begin
                frame_timer <= frame_timer + 1'b1;
            end

            case (state)
                // -----------------------------------------------------------
                ST_IDLE: begin
                    m_wb_cyc <= 1'b0;
                    m_wb_stb <= 1'b0;

                    if (cfg_start_pulse && (cfg_frame_count != 32'd0)) begin
                        reg_base_addr      <= cfg_base_addr;
                        reg_led_count      <= cfg_led_count;
                        reg_frame_count    <= cfg_frame_count;
                        reg_frame_interval <= cfg_frame_interval;
                        reg_loop           <= cfg_loop;

                        byte_addr             <= cfg_base_addr;
                        word_valid             <= 1'b0;
                        status_current_frame  <= 32'd0;
                        status_busy            <= 1'b1;

                        state <= ST_FRAME_BEGIN;
                    end
                end

                // -----------------------------------------------------------
                ST_FRAME_BEGIN: begin
                    frame_timer     <= 32'd0;
                    px_idx          <= 16'd0;
                    byte_sub_idx    <= 2'd0;
                    led_num_leds    <= reg_led_count;
                    led_start       <= 1'b1;

                    if (reg_led_count == 16'd0) begin
                        // Degenerate empty frame: nothing to send, treat as immediately done.
                        state <= ST_FRAME_TIMING_GATE;
                    end else begin
                        state <= ST_FETCH_BYTE;
                    end
                end

                // -----------------------------------------------------------
                // Sequential byte-stream reader: pulls bytes 0(R),1(G),2(B)
                // of the current pixel, fetching a fresh Wishbone word
                // whenever the previous one has been fully consumed.
                // -----------------------------------------------------------
                ST_FETCH_BYTE: begin
                    if (!word_valid) begin
                        m_wb_adr <= byte_addr[31:2];
                        m_wb_cyc <= 1'b1;
                        m_wb_stb <= 1'b1;
                        m_wb_we  <= 1'b0;
                        m_wb_sel <= 4'b1111;
                        state    <= ST_WORD_WAIT;
                    end else begin
                        case (byte_sub_idx)
                            2'd0: pix_r <= cur_byte_corrected;
                            2'd1: pix_g <= cur_byte_corrected;
                            default: ; // handled below (blue -> present pixel)
                        endcase

                        byte_addr <= byte_addr + 1'b1;
                        if (byte_addr[1:0] == 2'b11) begin
                            word_valid <= 1'b0; // crossing a word boundary
                        end

                        if (byte_sub_idx == 2'd2) begin
                            led_pixel_data  <= {pix_r, pix_g, cur_byte_corrected};
                            led_pixel_valid <= 1'b1;
                            byte_sub_idx    <= 2'd0;
                            state           <= ST_PRESENT_PIXEL;
                        end else begin
                            byte_sub_idx <= byte_sub_idx + 1'b1;
                        end
                    end
                end

                ST_WORD_WAIT: begin
                    if (m_wb_ack) begin
                        word_buf   <= m_wb_dat_r;
                        word_valid <= 1'b1;
                        m_wb_cyc   <= 1'b0;
                        m_wb_stb   <= 1'b0;
                        state      <= ST_FETCH_BYTE;
                    end
                end

                // -----------------------------------------------------------
                // Hand the assembled pixel to the WS2812 driver; once it is
                // consumed, either gather the next pixel or wait for the
                // driver to finish transmitting the whole frame.
                // -----------------------------------------------------------
                ST_PRESENT_PIXEL: begin
                    if (led_pixel_ready) begin
                        led_pixel_valid <= 1'b0;
                        if (px_idx == reg_led_count - 16'd1) begin
                            state <= ST_FRAME_WAIT_DONE;
                        end else begin
                            px_idx <= px_idx + 16'd1;
                            state  <= ST_FETCH_BYTE;
                        end
                    end
                end

                ST_FRAME_WAIT_DONE: begin
                    if (led_done) begin
                        state <= ST_FRAME_TIMING_GATE;
                    end
                end

                // -----------------------------------------------------------
                // Pace to the configured frame rate, then fire the
                // completion IRQ for the frame that just finished (its
                // index is still in status_current_frame at this point --
                // the actual index update happens next cycle, in
                // ST_ADVANCE_FRAME, so a reader sampling status_current_frame
                // alongside this pulse sees the frame that was just shown,
                // not the upcoming one).
                // -----------------------------------------------------------
                ST_FRAME_TIMING_GATE: begin
                    if (!cfg_enable) begin
                        status_busy <= 1'b0;
                        state       <= ST_IDLE;
                    end else if (frame_timer >= reg_frame_interval) begin
                        irq_frame_done <= 1'b1;
                        state          <= ST_ADVANCE_FRAME;
                    end
                end

                // -----------------------------------------------------------
                // Advance (or stop/loop). byte_addr is already correctly
                // positioned for the next frame from sequential consumption
                // -- only a loop-wrap needs to reset it to the base address.
                // -----------------------------------------------------------
                ST_ADVANCE_FRAME: begin
                    if (status_current_frame == reg_frame_count - 32'd1) begin
                        if (reg_loop) begin
                            status_current_frame <= 32'd0;
                            byte_addr             <= reg_base_addr;
                            word_valid             <= 1'b0;
                            state                  <= ST_FRAME_BEGIN;
                        end else begin
                            status_busy <= 1'b0;
                            state       <= ST_IDLE;
                        end
                    end else begin
                        status_current_frame <= status_current_frame + 1'b1;
                        state                  <= ST_FRAME_BEGIN;
                    end
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule

/*
 * Tang Nano 9K WS2812B LED Animation Player
 *
 * Reads a .fled animation file from the SDCard (SPI mode + FatFs, see
 * fled.c), loads its raw RGB frame payload into a small on-chip SRAM
 * buffer, then hands it to the autonomous LedAnimator DMA/WS2812B
 * hardware core (see led_animator.c), which streams it out to a WS2812B
 * strip at the file's declared frame rate entirely on its own (no CPU
 * polling needed once started).
 */

#include <stdio.h>
#include <stdint.h>

#include <generated/csr.h>
#include <libbase/uart.h>

#include "fled.h"
#include "led_animator.h"
#include "rainbow.h"

#define FLED_FILENAME "ANIM.FLED"

// On-chip SRAM is tight (a few KB) -- this comfortably covers small test
// animations (e.g. the 400-byte ANIM.FLED on the dev SD card) without
// needing HyperRAM/PSRAM at all.
#define FLED_MAX_PAYLOAD 2048
static uint8_t fled_payload[FLED_MAX_PAYLOAD] __attribute__((aligned(4)));

int main(void) {
    uart_init();

    printf("\n==========================================================\n");
    printf("   Sipeed Tang Nano 9K - WS2812B LED Animation Player\n");
    printf("   RISC-V VexRiscv (minimal) + SPI SDCard + LedAnimator DMA\n");
    printf("==========================================================\n\n");

#ifndef CSR_SPISDCARD_BASE
    printf("[FAIL] No SPI SDCard peripheral in this bitstream.\n");
    return 1;
#else
    fled_header_t hdr;
    uint32_t payload_size;
    int fr = fled_load(FLED_FILENAME, fled_payload, FLED_MAX_PAYLOAD, &hdr, &payload_size);
    if (fr == FLED_ERR_NO_SDCARD) {
        printf("\n[FALLBACK] No SDCard detected -- playing built-in rainbow fade instead.\n");
        payload_size = rainbow_generate(fled_payload, FLED_MAX_PAYLOAD);
        if (payload_size == 0) {
            printf("   [FAIL] Rainbow buffer does not fit (check RAINBOW_LED_COUNT).\n");
            return 1;
        }
        hdr.fps         = RAINBOW_FPS;
        hdr.led_count   = RAINBOW_LED_COUNT;
        hdr.frame_count = RAINBOW_FRAME_COUNT;
        printf("   [PASS] Generated %lu bytes (%u LEDs x %u frames).\n\n",
               (unsigned long)payload_size, RAINBOW_LED_COUNT, RAINBOW_FRAME_COUNT);
    } else if (fr != FLED_OK) {
        return 1;
    }

    printf("\n[STEP 4] Configuring LedAnimator hardware and starting playback...\n");
    uint32_t frame_interval = (uint32_t)(CONFIG_CLOCK_FREQUENCY / hdr.fps);

    led_animator_start((uint32_t)(uintptr_t)fled_payload, hdr.led_count,
                        hdr.frame_count, frame_interval, 1);

    printf("   frame_interval = %lu sys_clk cycles (%luHz / %uFPS)\n",
           (unsigned long)frame_interval, (unsigned long)CONFIG_CLOCK_FREQUENCY, hdr.fps);
    printf("   [PASS] Playback started (looping). Hardware streams frames autonomously.\n\n");

    printf("[STEP 5] Monitoring playback (debug: runs forever, prints busy/\n");
    printf("          current_frame once per second so a stall is visible)...\n\n");

    for (uint32_t i = 0; ; i++) {
        uint32_t busy = led_animator_is_busy();
        uint32_t frame = led_animator_get_current_frame();
        printf("   [%4lu] busy=%lu  current_frame=%lu\n", (unsigned long)i, (unsigned long)busy, (unsigned long)frame);
        for (volatile uint32_t d = 0; d < 5000000; d++);
    }
#endif
}

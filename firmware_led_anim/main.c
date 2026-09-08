/*
 * Tang Nano 9K WS2812B LED Animation Player
 *
 * Reads a .fled animation file from the SDCard (SPI mode + FatFs), loads
 * its raw RGB frame payload into a small on-chip SRAM buffer, then hands
 * it to the autonomous LedAnimator DMA/WS2812B hardware core, which
 * streams it out to a WS2812B strip at the file's declared frame rate
 * entirely on its own (no CPU polling needed once started).
 *
 * .fled format:
 *   0x00-0x03  Magic "FLED"
 *   0x04       Version (1)
 *   0x05       Target frame rate (FPS)
 *   0x06-0x07  LED count (uint16 LE)
 *   0x08-0x0B  Total frame count (uint32 LE)
 *   0x0C-0x0F  Flags/reserved
 *   0x10+      Raw RGB payload: frame_count * led_count * 3 bytes
 */

#include <stdio.h>
#include <stdint.h>

#include <generated/csr.h>
#include <libbase/uart.h>

#include <libfatfs/ff.h>
#include <liblitesdcard/spisdcard.h>

#define FLED_FILENAME   "ANIM.FLED"
#define FLED_HEADER_LEN 16

// On-chip SRAM is tight (a few KB) -- this comfortably covers small test
// animations (e.g. the 400-byte ANIM.FLED on the dev SD card) without
// needing HyperRAM/PSRAM at all.
#define FLED_MAX_PAYLOAD 2048
static uint8_t fled_payload[FLED_MAX_PAYLOAD] __attribute__((aligned(4)));

static const char *fresult_str(FRESULT fr) {
    switch (fr) {
        case FR_OK:            return "OK";
        case FR_DISK_ERR:      return "disk error";
        case FR_NOT_READY:     return "drive not ready";
        case FR_NO_FILE:       return "no file";
        case FR_NO_FILESYSTEM: return "no valid FAT filesystem";
        default:               return "error";
    }
}

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
    printf("[STEP 1] Mounting SDCard...\n");
    fatfs_set_ops_spisdcard();
    static FATFS fs;
    FRESULT fr = f_mount(&fs, "", 1);
    if (fr != FR_OK) {
        printf("   [FAIL] Mount failed: %s\n", fresult_str(fr));
        return 1;
    }
    printf("   [PASS] SDCard mounted.\n\n");

    printf("[STEP 2] Opening %s...\n", FLED_FILENAME);
    static FIL file;
    fr = f_open(&file, FLED_FILENAME, FA_READ);
    if (fr != FR_OK) {
        printf("   [FAIL] %s: %s\n", FLED_FILENAME, fresult_str(fr));
        f_mount(0, "", 0);
        return 1;
    }

    uint8_t header[FLED_HEADER_LEN];
    unsigned int nread = 0;
    fr = f_read(&file, header, FLED_HEADER_LEN, &nread);
    if (fr != FR_OK || nread != FLED_HEADER_LEN) {
        printf("   [FAIL] Could not read %u-byte header (got %u bytes, %s)\n",
               FLED_HEADER_LEN, nread, fresult_str(fr));
        f_close(&file);
        f_mount(0, "", 0);
        return 1;
    }

    if (header[0] != 'F' || header[1] != 'L' || header[2] != 'E' || header[3] != 'D') {
        printf("   [FAIL] Bad magic: %02X %02X %02X %02X (expected 'FLED')\n",
               header[0], header[1], header[2], header[3]);
        f_close(&file);
        f_mount(0, "", 0);
        return 1;
    }

    uint8_t  version   = header[4];
    uint8_t  fps        = header[5];
    uint16_t led_count = (uint16_t)header[6] | ((uint16_t)header[7] << 8);
    uint32_t frame_count = (uint32_t)header[8] | ((uint32_t)header[9] << 8) |
                           ((uint32_t)header[10] << 16) | ((uint32_t)header[11] << 24);

    printf("   [PASS] Magic OK. Version=%u  FPS=%u  LEDs=%u  Frames=%lu\n",
           version, fps, led_count, (unsigned long)frame_count);

    if (fps == 0) {
        printf("   [FAIL] FPS field is 0\n");
        f_close(&file);
        f_mount(0, "", 0);
        return 1;
    }

    uint32_t payload_size = (uint32_t)led_count * 3U * frame_count;
    printf("   Payload size: %lu bytes (buffer capacity: %u bytes)\n",
           (unsigned long)payload_size, FLED_MAX_PAYLOAD);

    if (payload_size == 0 || payload_size > FLED_MAX_PAYLOAD) {
        printf("   [FAIL] Payload size out of range for the on-chip buffer.\n");
        f_close(&file);
        f_mount(0, "", 0);
        return 1;
    }

    printf("\n[STEP 3] Loading animation payload into SRAM...\n");
    fr = f_read(&file, fled_payload, payload_size, &nread);
    f_close(&file);
    f_mount(0, "", 0);
    if (fr != FR_OK || nread != payload_size) {
        printf("   [FAIL] Payload read: got %u/%lu bytes (%s)\n",
               nread, (unsigned long)payload_size, fresult_str(fr));
        return 1;
    }
    printf("   [PASS] Loaded %lu bytes into SRAM buffer at 0x%08lX\n",
           (unsigned long)payload_size, (unsigned long)(uintptr_t)fled_payload);

    printf("\n[STEP 4] Configuring LedAnimator hardware and starting playback...\n");
    uint32_t frame_interval = (uint32_t)(CONFIG_CLOCK_FREQUENCY / fps);

    led_animator_base_addr_write((uint32_t)(uintptr_t)fled_payload);
    led_animator_led_count_write(led_count);
    led_animator_frame_count_write(frame_count);
    led_animator_frame_interval_write(frame_interval);
    led_animator_loop_write(1);
    led_animator_enable_write(1); // write-strobe: (re)starts playback from frame 0

    printf("   frame_interval = %lu sys_clk cycles (%luHz / %uFPS)\n",
           (unsigned long)frame_interval, (unsigned long)CONFIG_CLOCK_FREQUENCY, fps);
    printf("   [PASS] Playback started (looping). Hardware streams frames autonomously.\n\n");

    printf("[STEP 5] Monitoring playback (no LEDs attached yet -- this just proves\n");
    printf("          the DMA + frame-rate timer are alive in hardware)...\n\n");

    for (int i = 0; i < 20; i++) {
        uint32_t busy = led_animator_busy_read();
        uint32_t frame = led_animator_current_frame_read();
        printf("   [%2d] busy=%lu  current_frame=%lu\n", i, (unsigned long)busy, (unsigned long)frame);
        for (volatile uint32_t d = 0; d < 500000; d++);
    }

    printf("\n==========================================================\n");
    printf("   LED animation player running. Attach a WS2812B strip to\n");
    printf("   J7 pin 49 to see it.\n");
    printf("==========================================================\n");
    return 0;
#endif
}

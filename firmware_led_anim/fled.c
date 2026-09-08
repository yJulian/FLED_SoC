#include "fled.h"

#include <stdio.h>

#include <libfatfs/ff.h>
#include <liblitesdcard/spisdcard.h>

#define FLED_HEADER_LEN 16

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

int fled_load(const char *filename, uint8_t *buf, uint32_t buf_cap,
              fled_header_t *hdr, uint32_t *payload_size) {
    printf("[STEP 1] Mounting SDCard...\n");
    fatfs_set_ops_spisdcard();
    static FATFS fs;
    FRESULT fr = f_mount(&fs, "", 1);
    if (fr != FR_OK) {
        printf("   [FAIL] Mount failed: %s\n", fresult_str(fr));
        return FLED_ERR_NO_SDCARD;
    }
    printf("   [PASS] SDCard mounted.\n\n");

    printf("[STEP 2] Opening %s...\n", filename);
    static FIL file;
    fr = f_open(&file, filename, FA_READ);
    if (fr != FR_OK) {
        printf("   [FAIL] %s: %s\n", filename, fresult_str(fr));
        f_mount(0, "", 0);
        return FLED_ERR;
    }

    uint8_t header[FLED_HEADER_LEN];
    unsigned int nread = 0;
    fr = f_read(&file, header, FLED_HEADER_LEN, &nread);
    if (fr != FR_OK || nread != FLED_HEADER_LEN) {
        printf("   [FAIL] Could not read %u-byte header (got %u bytes, %s)\n",
               FLED_HEADER_LEN, nread, fresult_str(fr));
        f_close(&file);
        f_mount(0, "", 0);
        return FLED_ERR;
    }

    if (header[0] != 'F' || header[1] != 'L' || header[2] != 'E' || header[3] != 'D') {
        printf("   [FAIL] Bad magic: %02X %02X %02X %02X (expected 'FLED')\n",
               header[0], header[1], header[2], header[3]);
        f_close(&file);
        f_mount(0, "", 0);
        return FLED_ERR;
    }

    hdr->version     = header[4];
    hdr->fps         = header[5];
    hdr->led_count   = (uint16_t)header[6] | ((uint16_t)header[7] << 8);
    hdr->frame_count = (uint32_t)header[8] | ((uint32_t)header[9] << 8) |
                       ((uint32_t)header[10] << 16) | ((uint32_t)header[11] << 24);

    printf("   [PASS] Magic OK. Version=%u  FPS=%u  LEDs=%u  Frames=%lu\n",
           hdr->version, hdr->fps, hdr->led_count, (unsigned long)hdr->frame_count);

    if (hdr->fps == 0) {
        printf("   [FAIL] FPS field is 0\n");
        f_close(&file);
        f_mount(0, "", 0);
        return FLED_ERR;
    }

    uint32_t size = (uint32_t)hdr->led_count * 3U * hdr->frame_count;
    printf("   Payload size: %lu bytes (buffer capacity: %lu bytes)\n",
           (unsigned long)size, (unsigned long)buf_cap);

    if (size == 0 || size > buf_cap) {
        printf("   [FAIL] Payload size out of range for the RAM buffer.\n");
        f_close(&file);
        f_mount(0, "", 0);
        return FLED_ERR;
    }

    printf("\n[STEP 3] Loading animation payload into RAM...\n");
    fr = f_read(&file, buf, size, &nread);
    f_close(&file);
    f_mount(0, "", 0);
    if (fr != FR_OK || nread != size) {
        printf("   [FAIL] Payload read: got %u/%lu bytes (%s)\n",
               nread, (unsigned long)size, fresult_str(fr));
        return FLED_ERR;
    }
    printf("   [PASS] Loaded %lu bytes into RAM buffer at 0x%08lX\n",
           (unsigned long)size, (unsigned long)(uintptr_t)buf);

    *payload_size = size;
    return FLED_OK;
}

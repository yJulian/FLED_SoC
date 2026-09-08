/*
 * Tang Nano 9K SDCard Feasibility Test
 *
 * Mounts the FAT filesystem on the SPI-mode SDCard slot and lists the
 * contents of the root directory over the UART console. This is a
 * proof-of-concept to check whether a small RISC-V core (VexRiscv "minimal")
 * plus the SPI SDCard interface on this board are enough to read the card.
 */

#include <stdio.h>
#include <stdint.h>

#include <generated/csr.h>
#include <libbase/uart.h>

#include <libfatfs/ff.h>
#include <liblitesdcard/spisdcard.h>

static const char *fresult_str(FRESULT fr) {
    switch (fr) {
        case FR_OK:                  return "OK";
        case FR_DISK_ERR:            return "disk error";
        case FR_INT_ERR:             return "internal error";
        case FR_NOT_READY:           return "drive not ready";
        case FR_NO_FILE:             return "no file";
        case FR_NO_PATH:             return "no path";
        case FR_NO_FILESYSTEM:       return "no valid FAT filesystem";
        default:                     return "error";
    }
}

int main(void) {
    uart_init();

    printf("\n==========================================================\n");
    printf("   Sipeed Tang Nano 9K - SDCard Root Directory Listing Test\n");
    printf("   RISC-V VexRiscv (minimal) core + SPI-mode SDCard + FatFs\n");
    printf("==========================================================\n\n");

#ifndef CSR_SPISDCARD_BASE
    printf("[FAIL] No SPI SDCard peripheral in this bitstream (CSR_SPISDCARD_BASE undefined).\n");
    return 1;
#else
    printf("[STEP 1] Initializing SPI SDCard interface...\n");
    fatfs_set_ops_spisdcard();

    static FATFS fs;
    FRESULT fr = f_mount(&fs, "", 1);
    if (fr != FR_OK) {
        printf("   [FAIL] Mount failed: %s (FatFs error %d)\n", fresult_str(fr), fr);
        printf("   -> Check that a FAT/FAT32-formatted SDCard is inserted.\n");
        return 1;
    }
    printf("   [PASS] SDCard detected and FAT filesystem mounted.\n\n");

    printf("[STEP 2] Listing root directory (\"/\")...\n\n");
    static DIR dir;
    fr = f_opendir(&dir, "/");
    if (fr != FR_OK) {
        printf("   [FAIL] f_opendir failed: %s (FatFs error %d)\n", fresult_str(fr), fr);
        f_mount(0, "", 0);
        return 1;
    }

    unsigned int file_count = 0, dir_count = 0;
    static FILINFO fno;
    for (;;) {
        fr = f_readdir(&dir, &fno);
        if (fr != FR_OK || fno.fname[0] == 0)
            break;

        if (fno.fattrib & AM_DIR) {
            printf("   <DIR>  %s\n", fno.fname);
            dir_count++;
        } else {
            printf("   %8lu  %s\n", (unsigned long)fno.fsize, fno.fname);
            file_count++;
        }
    }
    f_closedir(&dir);
    f_mount(0, "", 0);

    printf("\n   -> %u file(s), %u director%s found.\n\n",
           file_count, dir_count, (dir_count == 1) ? "y" : "ies");

    printf("==========================================================\n");
    printf("   [SUCCESS] SDCard root listing complete.\n");
    printf("==========================================================\n");
    return 0;
#endif
}

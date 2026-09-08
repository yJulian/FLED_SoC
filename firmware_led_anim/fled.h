/*
 * .fled animation file format + SDCard loader.
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

#ifndef FLED_H
#define FLED_H

#include <stdint.h>

typedef struct {
    uint8_t  version;
    uint8_t  fps;
    uint16_t led_count;
    uint32_t frame_count;
} fled_header_t;

#define FLED_OK            0
#define FLED_ERR          -1  // file/header/payload problem (a card IS present)
#define FLED_ERR_NO_SDCARD -2 // SDCard mount failed -- no/unreadable card

// Mounts the SDCard, opens filename, validates the .fled header and reads
// its RGB payload into buf (buf_cap bytes max). Prints [STEP 1-3] progress
// in the firmware's usual style. Always unmounts the SDCard again before
// returning, regardless of outcome. Returns FLED_OK on success, or one of
// the FLED_ERR* codes above.
int fled_load(const char *filename, uint8_t *buf, uint32_t buf_cap,
              fled_header_t *hdr, uint32_t *payload_size);

#endif

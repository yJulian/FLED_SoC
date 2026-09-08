/*
 * Built-in fallback animation: a smooth rainbow fade, used when no
 * .fled file can be loaded from the SDCard (e.g. no card inserted) so
 * the strip still shows something instead of staying dark.
 */

#ifndef RAINBOW_H
#define RAINBOW_H

#include <stdint.h>

// Assumed strip length for the fallback animation, since there is no
// .fled header to declare the real one. Adjust to match your hardware
// (currently matches the 64-LED ANIM.FLED test strip). Frame count is
// capped by the firmware's 2KB SRAM payload buffer: at 64 LEDs that's
// 192 bytes/frame, so 10 frames (1920 bytes) is the practical max.
#define RAINBOW_LED_COUNT   64
#define RAINBOW_FRAME_COUNT 10
#define RAINBOW_FPS         10

// Dims the generated colors by this many bits (0 = full brightness,
// 3 = 1/8 brightness). Higher = darker.
#define RAINBOW_BRIGHTNESS_SHIFT 3

// Fills buf (buf_cap bytes) with RAINBOW_FRAME_COUNT frames of
// RAINBOW_LED_COUNT LEDs, all lit the same color, cycling once through
// the full hue wheel across the frames. Returns the payload size in
// bytes, or 0 if buf_cap is too small to hold it.
uint32_t rainbow_generate(uint8_t *buf, uint32_t buf_cap);

#endif

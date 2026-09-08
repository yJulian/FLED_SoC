#include "rainbow.h"

// Classic WS2812 "color wheel": maps 0-255 to a full rainbow, R->G->B->R.
static void wheel(uint8_t pos, uint8_t *r, uint8_t *g, uint8_t *b) {
    pos = 255 - pos;
    if (pos < 85) {
        *r = 255 - pos * 3;
        *g = 0;
        *b = pos * 3;
    } else if (pos < 170) {
        pos -= 85;
        *r = 0;
        *g = pos * 3;
        *b = 255 - pos * 3;
    } else {
        pos -= 170;
        *r = pos * 3;
        *g = 255 - pos * 3;
        *b = 0;
    }
}

uint32_t rainbow_generate(uint8_t *buf, uint32_t buf_cap) {
    uint32_t frame_size = (uint32_t)RAINBOW_LED_COUNT * 3U;
    uint32_t total_size  = frame_size * RAINBOW_FRAME_COUNT;
    if (total_size > buf_cap)
        return 0;

    for (uint32_t f = 0; f < RAINBOW_FRAME_COUNT; f++) {
        uint8_t r, g, b;
        wheel((uint8_t)((f * 256U) / RAINBOW_FRAME_COUNT), &r, &g, &b);
        r >>= RAINBOW_BRIGHTNESS_SHIFT;
        g >>= RAINBOW_BRIGHTNESS_SHIFT;
        b >>= RAINBOW_BRIGHTNESS_SHIFT;

        uint8_t *frame = buf + f * frame_size;
        for (uint32_t led = 0; led < RAINBOW_LED_COUNT; led++) {
            frame[led * 3 + 0] = r;
            frame[led * 3 + 1] = g;
            frame[led * 3 + 2] = b;
        }
    }

    return total_size;
}

/*
 * Driver for the autonomous LedAnimator DMA/WS2812B hardware core.
 * Once started, the core streams frames from SRAM to the WS2812B strip at
 * the configured frame rate entirely on its own -- no CPU polling needed.
 */

#ifndef LED_ANIMATOR_H
#define LED_ANIMATOR_H

#include <stdint.h>

// Configures and (re)starts playback from frame 0.
void led_animator_start(uint32_t base_addr, uint16_t led_count,
                         uint32_t frame_count, uint32_t frame_interval,
                         int loop);

uint32_t led_animator_is_busy(void);
uint32_t led_animator_get_current_frame(void);

// Hardware defaults to gamma=2.8 correction enabled; call with 0 to disable
// (e.g. to compare raw vs. gamma-corrected output).
void led_animator_set_gamma_enable(int enable);

#endif

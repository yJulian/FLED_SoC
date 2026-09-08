#include "led_animator.h"

#include <generated/csr.h>

void led_animator_start(uint32_t base_addr, uint16_t led_count,
                         uint32_t frame_count, uint32_t frame_interval,
                         int loop) {
    led_animator_base_addr_write(base_addr);
    led_animator_led_count_write(led_count);
    led_animator_frame_count_write(frame_count);
    led_animator_frame_interval_write(frame_interval);
    led_animator_loop_write(loop ? 1 : 0);
    led_animator_enable_write(1); // write-strobe: (re)starts playback from frame 0
}

uint32_t led_animator_is_busy(void) {
    return led_animator_busy_read();
}

uint32_t led_animator_get_current_frame(void) {
    return led_animator_current_frame_read();
}

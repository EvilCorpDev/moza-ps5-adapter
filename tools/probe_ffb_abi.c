// SPDX-License-Identifier: MIT
// Print the native Linux UAPI layout only; never open or write a device.
#include <linux/input.h>
#include <stddef.h>
#include <stdio.h>
int main(void) {
    struct ff_effect damper = {0};
    damper.type = FF_DAMPER;
    damper.id = 7;
    damper.direction = 0x4000;
    damper.replay.length = 150;
    damper.u.condition[0].right_saturation = 1024;
    damper.u.condition[0].left_saturation = 1024;
    damper.u.condition[0].right_coeff = -456;
    damper.u.condition[0].left_coeff = 123;
    printf("{\"ff_effect_bytes\":%zu,\"union_offset\":%zu,\"replay_offset\":%zu,"
           "\"input_event_bytes\":%zu,\"EVIOCSFF\":%lu,\"EVIOCRMFF\":%lu,"
           "\"condition_bytes\":%zu,\"condition_right_coeff_offset\":%zu,"
           "\"condition_left_coeff_offset\":%zu,\"damper_hex\":\"",
           sizeof(struct ff_effect), offsetof(struct ff_effect,u),
           offsetof(struct ff_effect,replay), sizeof(struct input_event),
           (unsigned long)EVIOCSFF, (unsigned long)EVIOCRMFF,
           sizeof(struct ff_condition_effect),
           offsetof(struct ff_condition_effect,right_coeff),
           offsetof(struct ff_condition_effect,left_coeff));
    for (size_t i = 0; i < sizeof(damper); ++i)
        printf("%02x", ((const unsigned char *)&damper)[i]);
    printf("\"}\n");
    return 0;
}

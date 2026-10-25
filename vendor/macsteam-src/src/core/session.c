#include "session.h"

static uint32_t g_account_id = 0;

uint32_t sx_session_account_id(void) {
    return g_account_id;
}

void sx_session_set_account_id(uint32_t account_id) {
    if (account_id && !g_account_id)
        g_account_id = account_id;
}

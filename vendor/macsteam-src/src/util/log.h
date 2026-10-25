// Logging
#ifndef MACSTEAM_UTIL_LOG_H
#define MACSTEAM_UTIL_LOG_H

#include <stdio.h>
#include <time.h>

extern int sx_log_level;
extern FILE *sx_log_file;
void sx_log_init(void);

#define SX_LVL_ERR  0
#define SX_LVL_WARN 1
#define SX_LVL_INFO 2
#define SX_LVL_DBG  3

#define SX_LOG_PREFIX "[macsteam]"

#define SX_LOG_IMPL(level, tag, fmt, ...) do { \
    if (sx_log_level >= (level)) { \
        time_t _t = time(NULL); \
        struct tm _tm; localtime_r(&_t, &_tm); \
        fprintf(stderr, "%02d:%02d:%02d " SX_LOG_PREFIX " %s " fmt "\n", \
                _tm.tm_hour, _tm.tm_min, _tm.tm_sec, tag, ##__VA_ARGS__); \
        if (sx_log_file) { \
            fprintf(sx_log_file, "%02d:%02d:%02d " SX_LOG_PREFIX " %s " fmt "\n", \
                    _tm.tm_hour, _tm.tm_min, _tm.tm_sec, tag, ##__VA_ARGS__); \
        } \
    } \
} while(0)

#define SX_ERR(fmt, ...)  SX_LOG_IMPL(SX_LVL_ERR,  "ERR", fmt, ##__VA_ARGS__)
#define SX_WARN(fmt, ...) SX_LOG_IMPL(SX_LVL_WARN, "WRN", fmt, ##__VA_ARGS__)
#define SX_LOG(fmt, ...)  SX_LOG_IMPL(SX_LVL_INFO, "INF", fmt, ##__VA_ARGS__)
#define SX_DBG(fmt, ...)  SX_LOG_IMPL(SX_LVL_DBG,  "DBG", fmt, ##__VA_ARGS__)

int sx_log_once_seen(const void *site, unsigned long key);

#define SX_LOG_ONCE_KEY(key, fmt, ...) do { \
    static const char _site_tag = 0; \
    if (sx_log_level >= SX_LVL_DBG || sx_log_once_seen(&_site_tag, (unsigned long)(key))) \
        SX_LOG(fmt, ##__VA_ARGS__); \
} while(0)

#define SX_LOG_ONCE(fmt, ...) SX_LOG_ONCE_KEY(0, fmt, ##__VA_ARGS__)

#endif // MACSTEAM_UTIL_LOG_H

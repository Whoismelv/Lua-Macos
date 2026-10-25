// Steam protocol constants
#ifndef MACSTEAM_CONSTANTS_H
#define MACSTEAM_CONSTANTS_H

// EMsg constants
#define EMSG_CLIENT_LICENSE_LIST   780
#define EMSG_MASK                 0x7FFFFFFFu

#define EMSG_CLIENT_GET_USER_STATS_RESPONSE  819u

#define USERSTATS_RESP_ERESULT_OK       1

#define SX_ACH_FALLBACK_UNLOCK_TIME 1191999600u

// Must match real CLicense size or Steam crashes at login
#define LIC_OBJ_SIZE_FALLBACK     336

#define RELEASE_STATE_RELEASED    4

#define UTLBUF_KEY_SIZE           32

// Fake license values
#define FAKE_PURCHASE_TIME        1577836800u
#define FAKE_PAYMENT_METHOD       1
#define FAKE_LICENSE_TYPE         1
#define FAKE_CHANGE_NUMBER        1
// Wrong owner_id makes DLC show as Family Library
#define LICENSE_OWNER_ID_FALLBACK 1u

#endif // MACSTEAM_CONSTANTS_H

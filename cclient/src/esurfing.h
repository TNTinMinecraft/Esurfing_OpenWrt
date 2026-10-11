#ifndef ESURFING_H
#define ESURFING_H

#include <stddef.h>
#include <stdint.h>
#include <signal.h>

#define MAX_PACKAGE (2u * 1024u * 1024u)
#define MAX_XML 32768u
#define URL_SIZE 4096
#define USER_AGENT "CCTP/WinSVR5/1069"
#define ZERO_ID "00000000-0000-0000-0000-000000000000"
#define PROBE "tarce log algo auto compstr null value"
#define RETRY_NETWORK (-2)
#define RETRY_SESSION (-3)
#define DETECT_URL "http://preportal.go189.cn/detect.html"

typedef struct {
    char id[37];
    int tea;
    int iv_length;
    unsigned char iv[8];
    uint32_t keys[3][4];
    uint64_t deskeys[6][16];
} Algorithm;

typedef struct {
    char interface[64], ip[16], mac[18], gateway[16], id[37], hostname[256];
    char ticket_url[URL_SIZE], auth_url[URL_SIZE];
    char keep_url[URL_SIZE], term_url[URL_SIZE], ticket[2048];
    Algorithm algorithm;
    int active, retries;
    unsigned retry_after;
} Client;

/* -1: local/protocol error, -2: transient network failure. No secret data in errors. */
extern char error_message[192];
extern volatile sig_atomic_t stopping, reconnecting;
int fail(const char *message);
void wipe(void *p, size_t n);
int copy_string(char *dst, size_t size, const char *src);
int valid_uuid(const char *s);
void hex_encode(const unsigned char *src, size_t n, char *dst, int upper);
void digest(const void *data, size_t n, unsigned char *out, int sha256);
void tea_block(unsigned char block[8], const uint32_t key[4], int decrypt, int big_endian);
void algorithm_init(Algorithm *a, const unsigned char key[48], int tea);
int algorithm_load(Algorithm *a, const unsigned char *package, size_t n);
char *encrypt_xml(const Algorithm *a, const char *xml);
char *decrypt_xml(const Algorithm *a, const char *body, size_t n);

int resolve_url(char out[URL_SIZE], const char *base, const char *relative);
int discover(Client *c, const char *url);
int negotiate(Client *c);
int login_client(Client *c, const char *username, const char *password);
int keep_client(Client *c);
int logout_client(Client *c);
int network_identity(Client *c);
void sleep_seconds(unsigned seconds);

#endif

#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#define _DARWIN_C_SOURCE
#include "esurfing.h"
#include <curl/curl.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <ifaddrs.h>
#include <sys/stat.h>
#include <sys/resource.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include <time.h>
#ifdef __linux__
#include <netpacket/packet.h>
#elif defined(__APPLE__)
#include <net/if_dl.h>
#endif

volatile sig_atomic_t stopping, reconnecting;
static void stop(int signal_number)
{
    if (signal_number==SIGHUP) reconnecting=1;
    else stopping=1;
}

static int read_line(FILE *f, char *out, size_t capacity)
{
    if (stopping || ferror(f) || feof(f)) return fail("Credential input ended or interrupted");
    size_t n=0; int ch;
    while ((ch=fgetc(f))!=EOF && ch!='\n') {
        if (!ch || n+1>=capacity) return fail("Credential line is invalid or too long");
        out[n++]=(char)ch;
    }
    if (ferror(f) || stopping) return fail("Credential input interrupted");
    if (n && out[n-1]=='\r') n--;
    out[n]=0;
    return n ? 0 : fail("Nonempty username and password are required");
}

static int credentials(const char *path, char user[512], char password[512])
{
    if (path) {
        int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
        if (fd<0) return fail("Cannot open credentials file");
        struct stat st;
        if (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() || (st.st_mode&077) || st.st_size>1024) {
            close(fd); return fail("Credentials must be an owned regular file, chmod 600, at most 1024 bytes");
        }
        FILE *f=fdopen(fd,"r");
        if (!f) { close(fd); return fail("Cannot read credentials file"); }
        int result=read_line(f,user,512) || read_line(f,password,512);
        if (!result && !ferror(f) && !feof(f) && fgetc(f)!=EOF) result=fail("Credentials file must have exactly two lines");
        fclose(f); return result ? -1 : 0;
    }
    FILE *tty=fopen("/dev/tty","r+");
    if (!tty) return fail("No terminal; supply --credentials FILE");
    dprintf(fileno(tty),"宽带账号: ");
    int result=read_line(tty,user,512);
    struct termios saved, hidden;
    if (!result) {
        if (tcgetattr(fileno(tty),&saved)) result=fail("Cannot disable password echo");
        else {
            hidden=saved; hidden.c_lflag&=(tcflag_t)~ECHO;
            if (tcsetattr(fileno(tty),TCSAFLUSH,&hidden)) result=fail("Cannot disable password echo");
            else {
                dprintf(fileno(tty),"宽带密码: ");
                result=read_line(tty,password,512);
                /* A signal only sets stopping; always restore terminal echo. */
                if (tcsetattr(fileno(tty),TCSANOW,&saved)) result=fail("Cannot restore terminal settings");
                dprintf(fileno(tty),"\n");
            }
        }
    }
    fclose(tty); return result;
}

/* A short outage retries keepalive first. A lost session or exhausted retries
 * returns to discovery, always refreshing IP/MAC/gateway and algorithm keys. */
static unsigned retry_delay(unsigned failures, unsigned base, unsigned maximum, uint32_t jitter)
{
    unsigned delay=base;
    while (failures-- && delay<maximum) delay=delay>maximum/2 ? maximum : delay*2;
    unsigned spread=delay/4;
    if (spread>maximum-delay) spread=maximum-delay;
    return delay+(spread ? jitter%(spread+1) : 0);
}

static time_t monotonic_seconds(void)
{
    struct timespec ts;
    return clock_gettime(CLOCK_MONOTONIC,&ts) ? 0 : ts.tv_sec;
}

static void end_session(Client *c)
{
    if (!c->active) return;
    puts("Logging out...");
    if (logout_client(c)) fprintf(stderr,"Logout not confirmed: %s\n",error_message);
    else puts("Logout accepted.");
    /* Failure to confirm logout must not retain stale session state. */
    c->active=0;
}

static int same_network(Client *c, const Client *configuration)
{
    Client *fresh=calloc(1,sizeof(*fresh));
    if (!fresh) return fail("Out of memory");
    *fresh=*configuration;
    int result=network_identity(fresh);
    if (result || strcmp(c->ip,fresh->ip) || strcmp(c->mac,fresh->mac) || strcmp(c->gateway,fresh->gateway)) {
        fail("Network address/link changed; rediscovering portal"); result=RETRY_NETWORK;
    }
    wipe(fresh,sizeof(*fresh)); free(fresh); return result;
}

#define RENEW_SESSION (-4)
typedef struct {
    int minute;                 /* Local HH:MM, or -1 when disabled. */
    time_t deadline;
} Schedule;

static int schedule_start(Schedule *schedule)
{
    schedule->deadline=0;
    if (schedule->minute>=0) {
        time_t now=time(NULL);
        struct tm local;
        if (now==(time_t)-1 || !localtime_r(&now,&local)) return fail("Cannot read local schedule time");
        local.tm_hour=schedule->minute/60; local.tm_min=schedule->minute%60;
        local.tm_sec=0; local.tm_isdst=-1;
        time_t due=mktime(&local);
        if (due==(time_t)-1) return fail("Cannot calculate daily reconnect time");
        if (due<=now) {
            local.tm_mday++; local.tm_hour=schedule->minute/60;
            local.tm_min=schedule->minute%60; local.tm_sec=0; local.tm_isdst=-1;
            due=mktime(&local);
        }
        if (due==(time_t)-1) return fail("Cannot calculate next daily reconnect time");
        schedule->deadline=due;
        char stamp[64];
        if (localtime_r(&due,&local) && strftime(stamp,sizeof(stamp),"%Y-%m-%d %H:%M:%S %Z",&local))
            printf("Next scheduled reauthentication: %s.\n",stamp);
    }
    return 0;
}

static int schedule_due(const Schedule *schedule)
{
    time_t now=time(NULL);
    return schedule->deadline && now>=schedule->deadline;
}

/* Check wall-clock schedules even during keepalive transport backoff.
 * This timer exists only in an active session; it cannot start a stopped or
 * permanently suspended service. HTTP calls retain their bounded timeout. */
static int session_sleep(unsigned seconds, const Schedule *schedule)
{
    time_t until=monotonic_seconds()+seconds;
    while (!stopping && !reconnecting) {
        if (schedule_due(schedule)) return RENEW_SESSION;
        time_t now=monotonic_seconds();
        if (now>=until) break;
        unsigned delay=(unsigned)(until-now);
        if (schedule->deadline && delay>30) delay=30;
        sleep_seconds(delay);
    }
    return 0;
}

/* Keep the server's keepalive schedule while checking the data path separately.
 * next_check persists across keepalives, including server intervals below 30s. */
static int wait_online(Client *c, const Client *configuration, unsigned interval,
                       const char *check_url, unsigned check_interval,
                       time_t *next_check, unsigned *misses, int *check_ok, const Schedule *schedule)
{
    time_t keep_due=monotonic_seconds()+interval;
    while (!stopping && !reconnecting) {
        if (schedule_due(schedule)) return RENEW_SESSION;
        time_t now=monotonic_seconds();
        if (now>=keep_due) return 0;
        if (check_interval && now>=*next_check) {
            int result=same_network(c,configuration);
            if (result) return result;
            result=check_connectivity(c,check_url);
            if (stopping || reconnecting) return 0;
            *next_check=monotonic_seconds()+check_interval;
            if (!result) {
                if (!*check_ok) puts("Internet connectivity check passed.");
                *misses=0; *check_ok=1;
            } else {
                *check_ok=0;
                fprintf(stderr,"Internet connectivity check failed (%u/3): %s\n",++*misses,error_message);
                if (*misses>=3) {
                    fail("Internet connectivity lost despite authentication keepalive; refreshing session");
                    return RETRY_SESSION;
                }
            }
            continue;
        }
        time_t due=keep_due;
        if (check_interval && *next_check<due) due=*next_check;
        int result=session_sleep((unsigned)(due-now),schedule);
        if (result) return result;
    }
    return 0;
}

static int run_client(const Client *configuration, const char *path, const char *portal,
                      unsigned base, unsigned maximum, int no_retry, int service,
                      const char *check_url, unsigned check_interval, Schedule schedule)
{
    Client *c=calloc(1,sizeof(*c));
    if (!c) return fail("Out of memory");
    char username[512]={0}, password[512]={0};
    unsigned failures=0;
    uint32_t random=(uint32_t)getpid()^(uint32_t)monotonic_seconds();
    int result=0;
    if (check_interval)
        printf("Internet connectivity checks enabled: every %u seconds, reconnect after 3 consecutive failures.\n",check_interval);
    while (!stopping) {
        reconnecting=0;
        wipe(c,sizeof(*c)); *c=*configuration;
        result=network_identity(c);
        if (result) { result=RETRY_NETWORK; goto session_done; }
        printf("Interface %s, IPv4 %s; discovering portal...\n",c->interface,c->ip);
        if (!*c->ticket_url && (result=discover(c,portal))) goto session_done;
        if (stopping || reconnecting) goto session_done;
        if ((result=negotiate(c))) goto session_done;
        if (stopping || reconnecting) goto session_done;
        puts(c->algorithm.aes ? "Algorithm: double AES-128-CBC" : c->algorithm.tea ? "Algorithm: triple modified TEA" :
             c->algorithm.iv_length ? "Algorithm: double 3DES-CBC" : "Algorithm: double 3DES");
        /* File credentials are re-read on every new session, then wiped after
         * login. Interactive credentials stay in RAM only for reconnection. */
        if ((path || !*username) && (result=credentials(path,username,password))) goto session_done;
        if (stopping || reconnecting) goto session_done;
        result=login_client(c,username,password);
        if (path) { wipe(username,sizeof(username)); wipe(password,sizeof(password)); }
        if (result) goto session_done;
        puts("Login accepted; starting keepalive (Internet access still needs checking).");
        if ((result=schedule_start(&schedule))) goto session_done;
        time_t online_since=monotonic_seconds();
        time_t next_check=online_since+check_interval;
        unsigned check_misses=0;
        int check_ok=0;
        unsigned successes=0;
        while (!stopping && !reconnecting) {
            if (schedule_due(&schedule)) { result=RENEW_SESSION; break; }
            if ((result=same_network(c,configuration))) break;
            int interval=RETRY_NETWORK;
            for (int attempt=0; attempt<c->retries && !stopping && !reconnecting; attempt++) {
                c->retry_after=0;
                interval=keep_client(c);
                if (interval!=RETRY_NETWORK || attempt+1==c->retries) break;
                random=random*1664525u+1013904223u;
                unsigned delay=retry_delay((unsigned)attempt,base,maximum,random);
                if (delay<c->retry_after) delay=c->retry_after;
                fprintf(stderr,"Keepalive transport failed; retry %d/%d in %u seconds.\n",attempt+1,c->retries,delay);
                result=session_sleep(delay,&schedule);
                if (result) break;
                if (!stopping && !reconnecting && (result=same_network(c,configuration))) break;
            }
            if (stopping || reconnecting) break;
            if (result || interval<1) { if (!result) result=interval; break; }
            if (successes<2) successes++;
            /* Do not reset after auth alone: rapidly dying sessions must back off. */
            if (successes>=2 && monotonic_seconds()-online_since>=120 &&
                (!check_interval || check_ok)) failures=0;
            printf("Keepalive accepted; next in %d seconds.\n",interval);
            result=wait_online(c,configuration,(unsigned)interval,check_url,check_interval,
                               &next_check,&check_misses,&check_ok,&schedule);
            if (result) break;
        }
        if (reconnecting) { fail("Network event; refreshing session"); result=RETRY_NETWORK; }
        if (stopping) result=0;
session_done:
        if (reconnecting && result==0 && !stopping) { fail("Network event; refreshing session"); result=RETRY_NETWORK; }
        if (result==RENEW_SESSION && !stopping) puts("Scheduled reauthentication: refreshing session.");
        else if (result && !stopping) fprintf(stderr,"Error: %s\n",error_message);
        unsigned retry_after=c->retry_after;
        end_session(c);
        if (stopping) { result=0; break; }
        if (result==RENEW_SESSION) continue;
        if (result!=RETRY_NETWORK && result!=RETRY_SESSION) {
            if (service) {
                /* procd respawn is for crashes, not repeated rejected passwords.
                 * HUP from network events cannot resume a rejected account. */
                wipe(username,sizeof(username)); wipe(password,sizeof(password));
                fputs("Authentication/configuration suspended; fix the problem and restart the service.\n",stderr);
                while (!stopping) { reconnecting=0; sleep_seconds(86400); }
                result=0;
            }
            break;
        }
        if (no_retry) break;
        random=random*1664525u+1013904223u;
        unsigned delay=retry_delay(failures,base,maximum,random);
        if (failures<32) failures++;
        if (delay<retry_after) delay=retry_after;
        reconnecting=0;
        fprintf(stderr,"Reconnecting in %u seconds (attempt %u).\n",delay,failures);
        sleep_seconds(delay);
    }
    wipe(username,sizeof(username)); wipe(password,sizeof(password));
    wipe(c,sizeof(*c)); free(c);
    return stopping ? 0 : result;
}

static void usage(void)
{
    puts("esurfing — minimal Jiangsu campus portal client\n"
         "Usage: esurfing --interface DEVICE [options]\n"
         "  -i, --interface DEVICE    Physical/L3 campus network interface\n"
         "  -c, --credentials FILE    Mode 600 UTF-8 file: username, then password\n"
         "  -g, --gateway IPv4        Override default gateway\n"
         "  -p, --portal-url URL      Override portal discovery URL\n"
         "      --ticket-url URL      Explicit endpoint (requires --auth-url)\n"
         "      --auth-url URL        Explicit endpoint (requires --ticket-url)\n"
         "      --retry-delay SEC     Initial retry delay (default 5)\n"
         "      --retry-max SEC       Maximum backoff (default 300)\n"
         "      --check-interval SEC  Internet check every 30s; 0 disables (otherwise 10..86400)\n"
         "      --check-url URL       Probe URL (default Microsoft Connect Test; also accepts empty 204)\n"
         "      --reconnect-at HH:MM  Reauthenticate daily in system local time (off by default)\n"
         "      --no-retry            Stop instead of starting a new session\n"
         "      --service             Suspend on permanent error; requires credentials file\n"
         "  -h, --help                Show this help\n"
         "SIGINT/SIGTERM logs out; SIGHUP wakes reconnect. Rejected accounts are not retried.");
}

static int seconds_option(const char *value, unsigned *out)
{
    char *end; errno=0; unsigned long n=strtoul(value,&end,10);
    if (!*value || *end || errno || n<1 || n>86400) return fail("Retry seconds must be 1..86400");
    *out=(unsigned)n; return 0;
}

int main(int argc, char **argv)
{
    Client *configuration=calloc(1,sizeof(*configuration));
    if (!configuration) { fputs("Out of memory\n",stderr); return 1; }
    Client *c=configuration;
    const char *path=NULL, *portal=DETECT_URL;
    const char *check_url=CHECK_URL;
    unsigned check_interval=30;
    Schedule schedule={.minute=-1};
    unsigned base=5, maximum=300; int no_retry=0, service=0;
    static const struct option opts[]={
        {"interface",required_argument,NULL,'i'}, {"credentials",required_argument,NULL,'c'},
        {"gateway",required_argument,NULL,'g'}, {"portal-url",required_argument,NULL,'p'},
        {"ticket-url",required_argument,NULL,1000}, {"auth-url",required_argument,NULL,1001},
        {"retry-delay",required_argument,NULL,1002}, {"retry-max",required_argument,NULL,1003},
        {"no-retry",no_argument,NULL,1004}, {"service",no_argument,NULL,1005},
        {"check-url",required_argument,NULL,1006}, {"check-interval",required_argument,NULL,1007},
        {"reconnect-at",required_argument,NULL,1008},
        {"help",no_argument,NULL,'h'}, {NULL,0,NULL,0}
    };
    int ch;
    opterr=0;
    while ((ch=getopt_long(argc,argv,"i:c:g:p:h",opts,NULL))!=-1) {
        switch (ch) {
        case 'i': if (copy_string(c->interface,sizeof(c->interface),optarg)) goto early; break;
        case 'c': path=optarg; break;
        case 'g': if (copy_string(c->gateway,sizeof(c->gateway),optarg)) goto early; break;
        case 'p': portal=optarg; break;
        case 1000: if (copy_string(c->ticket_url,sizeof(c->ticket_url),optarg)) goto early; break;
        case 1001: if (copy_string(c->auth_url,sizeof(c->auth_url),optarg)) goto early; break;
        case 1002: if (seconds_option(optarg,&base)) goto early; break;
        case 1003: if (seconds_option(optarg,&maximum)) goto early; break;
        case 1004: no_retry=1; break;
        case 1005: service=1; break;
        case 1006: check_url=optarg; break;
        case 1007:
            if (!strcmp(optarg,"0")) check_interval=0;
            else if (seconds_option(optarg,&check_interval) || check_interval<10) {
                fail("Check interval must be 0 or 10..86400 seconds"); goto early;
            }
            break;
        case 1008:
            if (strlen(optarg)!=5 || optarg[2]!=':' || optarg[0]<'0' || optarg[0]>'2' ||
                optarg[1]<'0' || optarg[1]>'9' || optarg[3]<'0' || optarg[3]>'5' ||
                optarg[4]<'0' || optarg[4]>'9' || (optarg[0]=='2' && optarg[1]>'3')) {
                fail("Reconnect time must be HH:MM (00:00..23:59)"); goto early;
            }
            schedule.minute=((optarg[0]-'0')*10+optarg[1]-'0')*60+(optarg[3]-'0')*10+optarg[4]-'0';
            break;
        case 'h': usage(); free(c); return 0;
        default: fail("Unknown/incomplete option; use --help"); goto early;
        }
    }
    if (optind!=argc || !*c->interface || (!!*c->ticket_url!=!!*c->auth_url) ||
        base>maximum || (service && (!path || no_retry))) {
        fail("Invalid options: supply interface, paired endpoints and valid retry bounds; service requires credentials"); goto early;
    }
    if (no_retry && schedule.minute>=0) {
        fail("--reconnect-at cannot be combined with --no-retry"); goto early;
    }
    struct in_addr address;
    char checked[URL_SIZE];
    if (*c->gateway && inet_pton(AF_INET,c->gateway,&address)!=1) { fail("Invalid gateway"); goto early; }
    if (resolve_url(checked,NULL,portal) || resolve_url(checked,NULL,check_url) || (*c->ticket_url &&
        (resolve_url(checked,NULL,c->ticket_url) || resolve_url(checked,NULL,c->auth_url)))) goto early;
    struct rlimit core_limit={0,0};
    if (setrlimit(RLIMIT_CORE,&core_limit)) { fail("Cannot disable core dumps"); goto early; }
    struct sigaction action={0}; action.sa_handler=stop; sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT,&action,NULL) || sigaction(SIGTERM,&action,NULL) || sigaction(SIGHUP,&action,NULL)) {
        fail("Cannot install signal handler"); goto early;
    }
    signal(SIGPIPE,SIG_IGN);
    setvbuf(stdout,NULL,_IOLBF,0);
    if (curl_global_init(CURL_GLOBAL_DEFAULT)) { fail("Cannot initialize libcurl"); goto early; }
    int result=run_client(configuration,path,portal,base,maximum,no_retry,service,check_url,check_interval,schedule);
    curl_global_cleanup(); wipe(c,sizeof(*c)); free(c); return result ? 1 : 0;
early:
    fprintf(stderr,"Error: %s\n",error_message); wipe(c,sizeof(*c)); free(c); return 1;
}

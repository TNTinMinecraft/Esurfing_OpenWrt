#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#define _DARWIN_C_SOURCE
#include "esurfing.h"
#include <curl/curl.h>
#include <expat.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <time.h>
#include <errno.h>
#include <limits.h>

static char *trim(char *s)
{
    while (isspace((unsigned char)*s)) s++;
    size_t n=strlen(s);
    while (n && isspace((unsigned char)s[n-1])) s[--n]=0;
    return s;
}

int resolve_url(char out[URL_SIZE], const char *base, const char *relative)
{
    int result=-1;
    CURLU *u=curl_url();
    char *scheme=NULL, *value=NULL, *part=NULL;
    if (!u) return fail("Out of memory");
    if (!relative || !*relative) goto done;
    for (const unsigned char *p=(const unsigned char *)relative; *p; p++) if (*p<33 || *p>126) goto done;
    if (base && curl_url_set(u,CURLUPART_URL,base,0)) goto done;
    if (curl_url_set(u,CURLUPART_URL,relative,0) || curl_url_get(u,CURLUPART_SCHEME,&scheme,0)) goto done;
    if (strcmp(scheme,"http") && strcmp(scheme,"https")) goto done;
    const CURLUPart forbidden[]={CURLUPART_USER,CURLUPART_PASSWORD,CURLUPART_FRAGMENT};
    for (size_t i=0; i<3; i++) {
        if (curl_url_get(u,forbidden[i],&part,0)==CURLUE_OK) goto done;
    }
    if (curl_url_get(u,CURLUPART_URL,&value,0)) goto done;
    result=copy_string(out,URL_SIZE,value);
done:
    curl_free(part); curl_free(value); curl_free(scheme); curl_url_cleanup(u);
    return result ? fail("Invalid or oversized HTTP(S) URL") : 0;
}

typedef struct {
    char *body;
    size_t n, capacity, header_size;
    long status;
    char code[24], location[URL_SIZE], refresh[URL_SIZE], retry_after[24];
    int bad;
} Response;

static size_t receive_body(char *data, size_t size, size_t count, void *arg)
{
    Response *r=arg;
    if (size && count>SIZE_MAX/size) return 0;
    size_t n=size*count;
    if (n>MAX_PACKAGE-r->n) { r->bad=1; return 0; }
    size_t needed=r->n+n+1;
    if (needed>r->capacity) {
        size_t capacity=r->capacity ? r->capacity*2 : 4096;
        if (capacity<needed) capacity=needed;
        if (capacity>MAX_PACKAGE+1) capacity=MAX_PACKAGE+1;
        char *next=realloc(r->body,capacity);
        if (!next) { r->bad=1; return 0; }
        r->body=next; r->capacity=capacity;
    }
    memcpy(r->body+r->n,data,n); r->n+=n; r->body[r->n]=0;
    return n;
}

static size_t receive_header(char *data, size_t size, size_t count, void *arg)
{
    Response *r=arg;
    if (size && count>SIZE_MAX/size) return 0;
    size_t n=size*count;
    if (n>65536-r->header_size) { r->bad=1; return 0; }
    r->header_size+=n;
    if (n>=5 && !memcmp(data,"HTTP/",5)) {
        r->code[0]=r->location[0]=r->refresh[0]=r->retry_after[0]=0;
        return n;
    }
    const struct { const char *name; char *value; size_t cap; } headers[]={
        {"Error-Code:",r->code,sizeof(r->code)}, {"Location:",r->location,sizeof(r->location)},
        {"Refresh:",r->refresh,sizeof(r->refresh)}, {"Retry-After:",r->retry_after,sizeof(r->retry_after)}
    };
    for (size_t i=0; i<4; i++) {
        size_t len=strlen(headers[i].name);
        if (n>=len && !strncasecmp(data,headers[i].name,len)) {
            size_t end=n;
            while (end>len && isspace((unsigned char)data[end-1])) end--;
            while (len<end && isspace((unsigned char)data[len])) len++;
            if (end-len>=headers[i].cap || headers[i].value[0] || memchr(data+len,0,end-len)) { r->bad=1; return 0; }
            memcpy(headers[i].value,data+len,end-len); headers[i].value[end-len]=0;
        }
    }
    return n;
}

static int bind_socket(void *arg, curl_socket_t fd, curlsocktype purpose)
{
    Client *c=arg;
    if (purpose!=CURLSOCKTYPE_IPCXN) return CURL_SOCKOPT_ERROR;
#ifdef __linux__
    if (setsockopt(fd,SOL_SOCKET,SO_BINDTODEVICE,c->interface,(socklen_t)strlen(c->interface)+1)) return CURL_SOCKOPT_ERROR;
#elif defined(__APPLE__)
    unsigned index=if_nametoindex(c->interface);
    if (!index || setsockopt(fd,IPPROTO_IP,IP_BOUND_IF,&index,sizeof(index))) return CURL_SOCKOPT_ERROR;
#endif
    return CURL_SOCKOPT_OK;
}

static int retryable_http(Client *c, const Response *r)
{
    if (r->status!=408 && r->status!=429 && r->status!=500 && r->status!=502 && r->status!=503 && r->status!=504) return 0;
    char *end; errno=0;
    unsigned long seconds=strtoul(r->retry_after,&end,10);
    if (*r->retry_after && !*end && !errno && seconds<=86400) c->retry_after=(unsigned)seconds;
    return 1;
}

static int http_request(Client *c, const char *url, const char *body, const char *id, Response *r)
{
    char checked[URL_SIZE];
    if (resolve_url(checked,NULL,url)) return -1;
    CURL *curl=curl_easy_init();
    struct curl_slist *headers=NULL;
    int result=-1;
    memset(r,0,sizeof(*r));
    if (!curl) return fail("Cannot initialize HTTP client");
#define OPT(k,v) do { if (curl_easy_setopt(curl,k,v)!=CURLE_OK) { fail("Cannot configure HTTP client"); goto done; } } while (0)
#define HEADER(v) do { struct curl_slist *next=curl_slist_append(headers,v); if (!next) { fail("Out of memory"); goto done; } headers=next; } while (0)
    OPT(CURLOPT_URL,checked);
    OPT(CURLOPT_PROXY,"");
    OPT(CURLOPT_IPRESOLVE,(long)CURL_IPRESOLVE_V4);
    OPT(CURLOPT_INTERFACE,c->ip);
    OPT(CURLOPT_SOCKOPTFUNCTION,bind_socket);
    OPT(CURLOPT_SOCKOPTDATA,c);
    OPT(CURLOPT_USERAGENT,USER_AGENT);
    OPT(CURLOPT_HTTP_VERSION,(long)CURL_HTTP_VERSION_1_1);
    OPT(CURLOPT_CONNECTTIMEOUT,10L);
    OPT(CURLOPT_TIMEOUT,15L);
    OPT(CURLOPT_NOSIGNAL,1L);
    OPT(CURLOPT_WRITEFUNCTION,receive_body);
    OPT(CURLOPT_WRITEDATA,r);
    OPT(CURLOPT_HEADERFUNCTION,receive_header);
    OPT(CURLOPT_HEADERDATA,r);
    /* Redirects are GET-only and handled explicitly by discover(). TLS verification
     * stays enabled; no environment proxies, cookies, verbose logs, or disk cache. */
    HEADER("Connection: close");
    if (body) {
        unsigned char hash[16]; char hex[33], field[100];
        digest(body,strlen(body),hash,0); hex_encode(hash,16,hex,0);
        snprintf(field,sizeof(field),"CDC-Checksum: %s",hex); HEADER(field);
        snprintf(field,sizeof(field),"Client-ID: %s",c->id); HEADER(field);
        snprintf(field,sizeof(field),"Algo-ID: %s",id); HEADER(field);
        HEADER("Content-Type:"); HEADER("Expect:");
        OPT(CURLOPT_POSTFIELDS,body);
        OPT(CURLOPT_POSTFIELDSIZE,(long)strlen(body));
    }
    OPT(CURLOPT_HTTPHEADER,headers);
    CURLcode code=curl_easy_perform(curl);
    if (code!=CURLE_OK) {
        if (r->bad) fail("HTTP response exceeds limits or has invalid headers");
        else {
            snprintf(error_message,sizeof(error_message),"HTTP transport failed (curl %d)",(int)code);
            if (code==CURLE_INTERFACE_FAILED || code==CURLE_COULDNT_RESOLVE_HOST || code==CURLE_COULDNT_CONNECT || code==CURLE_OPERATION_TIMEDOUT ||
                code==CURLE_SEND_ERROR || code==CURLE_RECV_ERROR || code==CURLE_GOT_NOTHING || code==CURLE_PARTIAL_FILE) result=-2;
        }
        goto done;
    }
    curl_easy_getinfo(curl,CURLINFO_RESPONSE_CODE,&r->status);
    if (!r->body) {
        r->body=calloc(1,1);
        if (!r->body) { fail("Out of memory"); goto done; }
    }
    result=0;
done:
    curl_slist_free_all(headers); curl_easy_cleanup(curl);
    if (result) { free(r->body); r->body=NULL; }
    return result;
#undef OPT
#undef HEADER
}

/* Only a few direct response children are needed. Expat checks UTF-8, entities,
 * matching tags and syntax; bounded depth/text and forbidden DTDs limit memory. */
static const char *field_names[]={"ticket","expire","keep-url","term-url","keep-retry","interval","level","value"};
enum { TICKET, EXPIRE, KEEP_URL, TERM_URL, RETRIES, INTERVAL, LEVEL, VALUE, FIELD_COUNT };
typedef struct {
    XML_Parser parser;
    const char *root;
    char values[FIELD_COUNT][URL_SIZE];
    unsigned seen, depth;
    int selected, bad;
} Document;

static void XMLCALL xml_start(void *arg, const char *name, const char **attrs)
{
    Document *d=arg;
    (void)attrs;
    d->depth++;
    if (d->depth>32 || (d->depth==1 && strcmp(name,d->root)) || !strcmp(name,"excheck") || !strcmp(name,"zexcheck")) goto bad;
    if (d->depth==2) {
        d->selected=-1;
        for (int i=0; i<FIELD_COUNT; i++) if (!strcmp(name,field_names[i])) {
            if (d->seen&(1u<<i)) goto bad;
            d->seen|=1u<<i; d->selected=i;
        }
    } else if (d->depth>2 && d->selected>=0) goto bad;
    return;
bad:
    d->bad=1; XML_StopParser(d->parser,XML_FALSE);
}

static void XMLCALL xml_end(void *arg, const char *name)
{
    Document *d=arg; (void)name;
    if (d->depth==2) d->selected=-1;
    d->depth--;
}

static void XMLCALL xml_text(void *arg, const char *text, int n)
{
    Document *d=arg;
    if (d->selected<0) return;
    char *value=d->values[d->selected];
    size_t len=strlen(value);
    if ((size_t)n>=URL_SIZE-len) { d->bad=1; XML_StopParser(d->parser,XML_FALSE); return; }
    memcpy(value+len,text,(size_t)n); value[len+(size_t)n]=0;
}

static void XMLCALL xml_doctype(void *arg, const char *name, const char *system, const char *public_id, int subset)
{
    Document *d=arg; (void)name; (void)system; (void)public_id; (void)subset;
    d->bad=1; XML_StopParser(d->parser,XML_FALSE);
}

static int parse_document(Document *d, const char *text, const char *root)
{
    memset(d,0,sizeof(*d)); d->selected=-1; d->root=root;
    size_t n=strlen(text);
    if (!n || n>MAX_XML) return fail("Invalid XML size");
    d->parser=XML_ParserCreate("UTF-8");
    if (!d->parser) return fail("Out of memory");
    XML_SetUserData(d->parser,d);
    XML_SetElementHandler(d->parser,xml_start,xml_end);
    XML_SetCharacterDataHandler(d->parser,xml_text);
    XML_SetStartDoctypeDeclHandler(d->parser,xml_doctype);
    int ok=XML_Parse(d->parser,text,(int)n,XML_TRUE)==XML_STATUS_OK && !d->bad;
    XML_ParserFree(d->parser); d->parser=NULL;
    if (!ok) return fail("Malformed XML or unsupported server check command");
    for (int i=0; i<FIELD_COUNT; i++) {
        char *v=trim(d->values[i]); memmove(d->values[i],v,strlen(v)+1);
    }
    return 0;
}

static int number(const Document *d, int field, int fallback, int low, int high)
{
    if (!(d->seen&(1u<<field))) return fallback<0 ? fail("Missing numeric response field") : fallback;
    const char *s=d->values[field]; char *end;
    errno=0; long n=strtol(s,&end,10);
    if (!*s || *end || errno || n<low || n>high) return fail("Invalid numeric response field");
    return (int)n;
}

static int extract_tag(const char *html, const char *tag, char out[URL_SIZE])
{
    char start[80], end[80];
    snprintf(start,sizeof(start),"<%s>",tag); snprintf(end,sizeof(end),"</%s>",tag);
    const char *p=strstr(html,start);
    if (!p) return 1;
    p+=strlen(start);
    const char *q=strstr(p,end);
    if (!q || q-p>=URL_SIZE) return fail("Invalid portal URL element");
    char fragment[URL_SIZE+64];
    snprintf(fragment,sizeof(fragment),"<config><value>%.*s</value></config>",(int)(q-p),p);
    Document *d=calloc(1,sizeof(*d));
    if (!d) return fail("Out of memory");
    int result=parse_document(d,fragment,"config");
    if (!result) result=copy_string(out,URL_SIZE,d->values[VALUE]);
    free(d); return result;
}

/* Narrow HTML attribute reader for meta refresh, allowing arbitrary attribute
 * order, case and either quote style. All destinations still pass URL validation. */
static int attribute(const char *p, const char *end, const char *wanted, char out[URL_SIZE])
{
    while (p<end) {
        while (p<end && (isspace((unsigned char)*p) || *p=='/')) p++;
        const char *name=p;
        while (p<end && !isspace((unsigned char)*p) && *p!='=' && *p!='/') p++;
        size_t len=(size_t)(p-name);
        while (p<end && isspace((unsigned char)*p)) p++;
        if (p==end || *p!='=') continue;
        p++; while (p<end && isspace((unsigned char)*p)) p++;
        char quote=(p<end && (*p=='\'' || *p=='"')) ? *p++ : 0;
        const char *value=p;
        while (p<end && (quote ? *p!=quote : !isspace((unsigned char)*p))) p++;
        size_t n=(size_t)(p-value);
        if (len==strlen(wanted) && !strncasecmp(name,wanted,len)) {
            if (n>=URL_SIZE) return fail("Oversized meta refresh");
            memcpy(out,value,n); out[n]=0; return 0;
        }
        if (quote && p<end) p++;
    }
    return 1;
}

static int refresh_url(char *value, char out[URL_SIZE])
{
    char *p=strchr(value,';'); p=p ? p+1 : value;
    p=trim(p);
    if (strncasecmp(p,"url",3)) return 1;
    p=trim(p+3); if (*p!='=') return 1;
    p=trim(p+1); size_t n=strlen(p);
    if (n>=2 && (*p=='\'' || *p=='"') && p[n-1]==*p) { p[n-1]=0; p++; }
    /* Common HTML URL ampersand escaping; other entities are handled in config XML. */
    char *dest=out;
    while (*p) {
        if (!strncmp(p,"&amp;",5)) { *dest++='&'; p+=5; }
        else *dest++=*p++;
    }
    *dest=0; return *out ? 0 : 1;
}

int discover(Client *c, const char *start)
{
    char current[URL_SIZE];
    if (resolve_url(current,NULL,start)) return -1;
    for (int step=0; step<12 && !stopping; step++) {
        Response r; int result=http_request(c,current,NULL,NULL,&r);
        if (result) return result;
        char ticket[URL_SIZE], auth[URL_SIZE], target[URL_SIZE]={0};
        if (r.status==200) {
            if (memchr(r.body,0,r.n)) { free(r.body); return fail("Invalid portal HTML"); }
            int t=extract_tag(r.body,"ticket-url",ticket), a=extract_tag(r.body,"auth-url",auth);
            if (t<0 || a<0) { free(r.body); return -1; }
            if (!t && !a) {
                result=resolve_url(c->ticket_url,current,ticket) || resolve_url(c->auth_url,current,auth);
                free(r.body); return result ? -1 : 0;
            }
            refresh_url(r.refresh,target);
            for (char *p=r.body; !*target && (p=strchr(p,'<')); p++) {
                if (strncasecmp(p,"<meta",5) || !isspace((unsigned char)p[5])) continue;
                char *end=strchr(p+5,'>'); if (!end) break;
                char equiv[URL_SIZE], content[URL_SIZE];
                if (!attribute(p+5,end,"http-equiv",equiv) && !strcasecmp(trim(equiv),"refresh") &&
                    !attribute(p+5,end,"content",content)) refresh_url(content,target);
            }
        } else if (r.status==301 || r.status==302 || r.status==303 || r.status==307 || r.status==308) {
            copy_string(target,sizeof(target),r.location);
        }
        int transient=retryable_http(c,&r);
        long status=r.status;
        free(r.body);
        if (transient) { fail("Portal temporarily unavailable"); return RETRY_NETWORK; }
        if (!*target) {
            fail("No portal configuration/redirect; waiting for network or check --portal-url");
            return status==200 ? RETRY_NETWORK : -1;
        }
        char next[URL_SIZE];
        if (resolve_url(next,current,target)) return -1;
        if (!strcmp(next,current)) return fail("Portal redirect loop");
        copy_string(current,sizeof(current),next);
    }
    return fail(stopping ? "Interrupted" : "Too many portal redirects");
}

static int post(Client *c, const char *url, const char *body, const char *id, Response *r, long *code)
{
    int result=http_request(c,url,body,id,r);
    if (result) return result;
    if (r->status!=200) {
        snprintf(error_message,sizeof(error_message),"Authentication endpoint returned HTTP %ld",r->status);
        int result=retryable_http(c,r) ? RETRY_NETWORK : -1;
        free(r->body); r->body=NULL; return result;
    }
    char *end; errno=0;
    *code=strtol(r->code,&end,10);
    size_t digits=strlen(r->code+(r->code[0]=='-'));
    if (!digits || digits>10 || *end || errno) {
        free(r->body); r->body=NULL; return fail("Missing/invalid Error-Code header");
    }
    for (const char *p=r->code+(r->code[0]=='-'); *p; p++) if (!isdigit((unsigned char)*p)) {
        free(r->body); r->body=NULL; return fail("Invalid Error-Code header");
    }
    return 0;
}

int negotiate(Client *c)
{
    Response r; long code;
    int result=post(c,c->ticket_url,PROBE,ZERO_ID,&r,&code);
    if (result) return result;
    result=code==200 ? algorithm_load(&c->algorithm,(unsigned char *)r.body,r.n) : fail("Server refused algorithm negotiation");
    free(r.body); return result;
}

static int append(char *out, const char *text)
{
    size_t n=strlen(out), more=strlen(text);
    if (n+more>MAX_XML) return fail("Request XML too large");
    memcpy(out+n,text,more+1); return 0;
}

static int element(char *xml, const char *name, const char *value)
{
    if (append(xml,"<") || append(xml,name) || append(xml,">")) return -1;
    for (const unsigned char *p=(const unsigned char *)value; *p; p++) {
        if (*p<32 && *p!='\t' && *p!='\r' && *p!='\n') return fail("Invalid XML character in input");
        const char *s=*p=='&' ? "&amp;" : *p=='<' ? "&lt;" : *p=='>' ? "&gt;" : NULL;
        char one[2]={(char)*p,0};
        if (append(xml,s ? s : one)) return -1;
    }
    return append(xml,"</") || append(xml,name) || append(xml,">\n") ? -1 : 0;
}

enum { ST_TICKET, ST_AUTH, ST_KEEP, ST_TERM };
static char *request_xml(Client *c, int stage, const char *username, const char *password)
{
    char *xml=calloc(1,MAX_XML+1);
    if (!xml) { fail("Out of memory"); return NULL; }
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC,&ts)) { free(xml); fail("Cannot read monotonic clock"); return NULL; }
    uint64_t ticks=(uint64_t)ts.tv_sec*1000000000+(uint64_t)ts.tv_nsec;
    uint64_t value=(ticks>>34)&0x3fffffff;
    char stamp[10];
    for (int i=0; i<9; i++) stamp[i]=(char)(((value>>(48-i*6))&63)+0x30);
    stamp[9]=0;
#define EL(k,v) do { if (element(xml,k,v)) goto bad; } while (0)
    append(xml,"<?xml version=\"1.0\" encoding=\"utf-8\"?><request>\n");
    if (stage==ST_TICKET) EL("ostag","Windows 8(9200),64 bit ");
    if (stage==ST_TERM) EL("reason","1");
    EL("local-time",stamp);
    if (stage==ST_TICKET) EL("gwip",c->gateway);
    if (stage==ST_TICKET || stage==ST_KEEP) { EL("mac",c->mac); EL("ipv6",""); EL("ipv4",c->ip); }
    if (stage!=ST_TICKET) EL("ticket",c->ticket);
    if (stage==ST_AUTH) { EL("passwd",password); EL("userid",username); }
    EL("client-id",c->id); EL("user-agent",USER_AGENT);
    if (stage==ST_TICKET) EL("host-name",c->hostname);
    if (append(xml,"</request>")) goto bad;
    /* Check UTF-8 too, without retaining a DOM or exposing input in errors. */
    XML_Parser parser=XML_ParserCreate("UTF-8");
    if (!parser) { fail("Out of memory"); goto bad; }
    int valid=XML_Parse(parser,xml,(int)strlen(xml),XML_TRUE)==XML_STATUS_OK;
    XML_ParserFree(parser);
    if (!valid) { fail("Input is not valid XML/UTF-8"); goto bad; }
    return xml;
bad:
    wipe(xml,MAX_XML+1); free(xml); return NULL;
#undef EL
}

static int exchange(Client *c, int stage, const char *url, const char *username, const char *password, Document *d)
{
    for (int i=0; i<3; i++) {
        char *xml=request_xml(c,stage,username,password);
        if (!xml) return -1;
        char *body=encrypt_xml(&c->algorithm,xml);
        wipe(xml,MAX_XML+1); free(xml);
        if (!body) return -1;
        Response r={0}; long code;
        int result=post(c,url,body,c->algorithm.id,&r,&code);
        wipe(body,strlen(body)); free(body);
        if (result) {
            if (stage==ST_KEEP && (r.status==301 || r.status==302 || r.status==303 ||
                r.status==307 || r.status==308 || r.status==401 || r.status==403)) return RETRY_SESSION;
            return result;
        }
        if (code==200) {
            result=algorithm_load(&c->algorithm,(unsigned char *)r.body,r.n);
            free(r.body);
            if (result) return result;
            continue;
        }
        if (code!=0) {
            snprintf(error_message,sizeof(error_message),"%s: server Error-Code=%ld",
                     stage==ST_TICKET?"ticket":stage==ST_AUTH?"auth":stage==ST_KEEP?"keep":"term",code);
            free(r.body); return stage==ST_KEEP ? RETRY_SESSION : -1;
        }
        if (stage==ST_TERM) { free(r.body); return 0; }
        xml=decrypt_xml(&c->algorithm,r.body,r.n);
        free(r.body);
        if (!xml) return -1;
        result=parse_document(d,xml,"response");
        wipe(xml,strlen(xml)); free(xml); return result;
    }
    return fail("Repeated algorithm updates; stopping");
}

int login_client(Client *c, const char *username, const char *password)
{
    Document *d=calloc(1,sizeof(*d));
    if (!d) return fail("Out of memory");
    int result=exchange(c,ST_TICKET,c->ticket_url,NULL,NULL,d);
    if (result) goto done;
    if (!*d->values[TICKET]) { result=fail("Missing ticket"); goto done; }
    if (copy_string(c->ticket,sizeof(c->ticket),d->values[TICKET]) || number(d,EXPIRE,-1,1,30*86400)<0) { result=-1; goto done; }
    if (stopping) { result=fail("Interrupted"); goto done; }
    result=exchange(c,ST_AUTH,c->auth_url,username,password,d);
    if (result) goto done;
    if (resolve_url(c->term_url,c->auth_url,d->values[TERM_URL])) { result=-1; goto done; }
    /* Once auth succeeds and term is known, even a malformed keep configuration
     * must go through the caller's best-effort logout path. */
    c->active=1;
    if (resolve_url(c->keep_url,c->auth_url,d->values[KEEP_URL])) { result=-1; goto done; }
    c->retries=number(d,RETRIES,5,1,20);
    if (c->retries<0) result=-1;
done:
    wipe(d,sizeof(*d)); free(d); return result;
}

int keep_client(Client *c)
{
    Document *d=calloc(1,sizeof(*d));
    if (!d) return fail("Out of memory");
    int result=exchange(c,ST_KEEP,c->keep_url,NULL,NULL,d);
    if (!result) {
        int level=number(d,LEVEL,-1,0,1000);
        if (level<0) result=-1;
        else if (level) result=fail("Unsupported keep level");
        else result=number(d,INTERVAL,-1,1,86400);
    }
    free(d); return result;
}

int logout_client(Client *c)
{
    int result=exchange(c,ST_TERM,c->term_url,NULL,NULL,NULL);
    if (!result) { c->active=0; wipe(c->ticket,sizeof(c->ticket)); }
    return result;
}

void sleep_seconds(unsigned seconds)
{
    /* Atomically unblock exit signals when sleeping, so a signal between the
     * flag check and the wait cannot leave us asleep for a full interval. */
    sigset_t blocked, previous;
    sigemptyset(&blocked); sigaddset(&blocked,SIGINT); sigaddset(&blocked,SIGTERM); sigaddset(&blocked,SIGHUP);
    if (sigprocmask(SIG_BLOCK,&blocked,&previous)) return;
    struct timespec timeout={(time_t)seconds,0};
    if (!stopping && !reconnecting) pselect(0,NULL,NULL,NULL,&timeout,&previous);
    sigprocmask(SIG_SETMASK,&previous,NULL);
}

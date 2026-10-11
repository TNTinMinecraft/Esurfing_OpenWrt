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
#ifdef __linux__
#include <netpacket/packet.h>
#elif defined(__APPLE__)
#include <net/if_dl.h>
#endif

int network_identity(Client *c)
{
    struct ifaddrs *interfaces;
    unsigned char mac[6]={0}; int got_mac=0;
    if (!if_nametoindex(c->interface) || getifaddrs(&interfaces)) return fail("Cannot read selected interface");
    for (struct ifaddrs *p=interfaces; p; p=p->ifa_next) {
        if (!p->ifa_addr || strcmp(p->ifa_name,c->interface)) continue;
        if (p->ifa_addr->sa_family==AF_INET && !c->ip[0]) {
            struct sockaddr_in *address=(struct sockaddr_in *)p->ifa_addr;
            inet_ntop(AF_INET,&address->sin_addr,c->ip,sizeof(c->ip));
        }
#ifdef __linux__
        if (p->ifa_addr->sa_family==AF_PACKET) {
            struct sockaddr_ll *address=(struct sockaddr_ll *)p->ifa_addr;
            if (address->sll_halen==6) { memcpy(mac,address->sll_addr,6); got_mac=1; }
        }
#elif defined(__APPLE__)
        if (p->ifa_addr->sa_family==AF_LINK) {
            struct sockaddr_dl *address=(struct sockaddr_dl *)p->ifa_addr;
            if (address->sdl_alen==6) { memcpy(mac,LLADDR(address),6); got_mac=1; }
        }
#endif
    }
    freeifaddrs(interfaces);
    if (!*c->ip) return fail("Selected interface has no IPv4 address");
    if (!got_mac || (mac[0]&1) || !memcmp(mac,"\0\0\0\0\0\0",6) || !memcmp(mac,"\2\0\0\0\0\0",6))
        return fail("Selected interface has no usable Ethernet MAC");
    snprintf(c->mac,sizeof(c->mac),"%02X-%02X-%02X-%02X-%02X-%02X",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
#ifdef __linux__
    if (!*c->gateway) {
        FILE *f=fopen("/proc/net/route","r");
        if (!f) return fail("Cannot read routes; supply --gateway");
        char line[512], interface[64]; unsigned long dest, gateway, flags, metric, best=ULONG_MAX;
        while (fgets(line,sizeof(line),f)) {
            if (sscanf(line,"%63s %lx %lx %lx %*u %*u %lu",interface,&dest,&gateway,&flags,&metric)!=5 ||
                strcmp(interface,c->interface) || dest || (flags&3)!=3 || metric>=best) continue;
            /* /proc prints the native integer representation of the network bytes;
             * assigning s_addr works on both little- and big-endian OpenWrt. */
            struct in_addr address; address.s_addr=(uint32_t)gateway;
            if (inet_ntop(AF_INET,&address,c->gateway,sizeof(c->gateway))) best=metric;
        }
        fclose(f);
    }
#endif
    struct in_addr address;
    if (!*c->gateway) return fail("No interface default gateway; supply --gateway (required on macOS)");
    if (inet_pton(AF_INET,c->gateway,&address)!=1) return fail("Invalid IPv4 gateway");
    if (gethostname(c->hostname,sizeof(c->hostname)-1)) return fail("Cannot read host name");
    /* Stable per MAC, without filesystem state or recurring flash writes. The
     * UUID variant/version bits use the version-8 application-defined layout. */
    char source[80], hex[33]; unsigned char hash[32];
    snprintf(source,sizeof(source),"esurfing-c-v1:%s",c->mac);
    digest(source,strlen(source),hash,1); hash[6]=(hash[6]&15)|0x80; hash[8]=(hash[8]&63)|0x80;
    hex_encode(hash,16,hex,0);
    snprintf(c->id,sizeof(c->id),"%.8s-%.4s-%.4s-%.4s-%.12s",hex,hex+8,hex+12,hex+16,hex+20);
    return 0;
}


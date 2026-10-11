/* Legacy wire cryptography, translated from the repository's Python reference.
 * Never load or execute code from a downloaded algorithm image. */
#include "esurfing.h"
#include <lzma.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>

char error_message[192];

int fail(const char *message)
{
    snprintf(error_message, sizeof(error_message), "%s", message);
    return -1;
}

void wipe(void *p, size_t n)
{
    volatile unsigned char *v = p;
    while (n--) *v++ = 0;
}

int copy_string(char *dst, size_t size, const char *src)
{
    size_t n = strlen(src);
    if (n >= size) return fail("Input or response field is too long");
    memmove(dst, src, n + 1);
    return 0;
}

int valid_uuid(const char *s)
{
    if (strlen(s) != 36) return 0;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (s[i] != '-') return 0;
        } else if (!isxdigit((unsigned char)s[i])) return 0;
    }
    return 1;
}

void hex_encode(const unsigned char *src, size_t n, char *dst, int upper)
{
    const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        dst[i * 2] = digits[src[i] >> 4];
        dst[i * 2 + 1] = digits[src[i] & 15];
    }
    dst[n * 2] = 0;
}

static uint32_t get32(const unsigned char *p, int be)
{
    if (be) return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
    return (uint32_t)p[3] << 24 | (uint32_t)p[2] << 16 | (uint32_t)p[1] << 8 | p[0];
}

static void put32(unsigned char *p, uint32_t x, int be)
{
    for (int i = 0; i < 4; i++) p[be ? 3-i : i] = (unsigned char)(x >> (i*8));
}

static uint32_t rol(uint32_t x, unsigned n) { return (x << n) | (x >> (32-n)); }
static uint32_t ror(uint32_t x, unsigned n) { return (x >> n) | (x << (32-n)); }

/* MD5 for CDC-Checksum; SHA-256 for checking exact known PE templates. */
void digest(const void *data, size_t n, unsigned char *out, int sha256)
{
    static const uint32_t md5k[64] = {
        0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,0xa8304613,0xfd469501,
        0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,0x6b901122,0xfd987193,0xa679438e,0x49b40821,
        0xf61e2562,0xc040b340,0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,
        0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,0x676f02d9,0x8d2a4c8a,
        0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,
        0x289b7ec6,0xeaa127fa,0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,
        0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,0xffeff47d,0x85845dd1,
        0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391
    };
    static const unsigned shifts[16] = {7,12,17,22,5,9,14,20,4,11,16,23,6,10,15,21};
    static const uint32_t shak[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    };
    uint32_t h[8] = {0x67452301,0xefcdab89,0x98badcfe,0x10325476,0,0,0,0};
    if (sha256) {
        const uint32_t initial[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                                   0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
        memcpy(h, initial, sizeof(h));
    }
    const unsigned char *src = data;
    size_t blocks = n / 64 + (n % 64 < 56 ? 1 : 2);
    for (size_t b = 0; b < blocks; b++) {
        unsigned char block[64] = {0};
        size_t start = b * 64;
        if (start < n) memcpy(block, src + start, n-start < 64 ? n-start : 64);
        if (start <= n && n-start < 64) block[n-start] = 0x80;
        if (b == blocks-1) {
            uint64_t bits = (uint64_t)n * 8;
            for (int i = 0; i < 8; i++) block[sha256 ? 63-i : 56+i] = (unsigned char)(bits >> (i*8));
        }
        uint32_t w[64];
        for (int i = 0; i < 16; i++) w[i] = get32(block+i*4, sha256);
        uint32_t a=h[0], bb=h[1], c=h[2], d=h[3], e=h[4], f=h[5], g=h[6], hh=h[7];
        if (sha256) {
            for (int i = 16; i < 64; i++)
                w[i] = w[i-16] + (ror(w[i-15],7)^ror(w[i-15],18)^(w[i-15]>>3)) + w[i-7]
                     + (ror(w[i-2],17)^ror(w[i-2],19)^(w[i-2]>>10));
            for (int i = 0; i < 64; i++) {
                uint32_t t1 = hh + (ror(e,6)^ror(e,11)^ror(e,25)) + ((e&f)^(~e&g)) + shak[i] + w[i];
                uint32_t t2 = (ror(a,2)^ror(a,13)^ror(a,22)) + ((a&bb)^(a&c)^(bb&c));
                hh=g; g=f; f=e; e=d+t1; d=c; c=bb; bb=a; a=t1+t2;
            }
        } else {
            for (unsigned i = 0; i < 64; i++) {
                uint32_t fn; unsigned index;
                if (i < 16) { fn=(bb&c)|(~bb&d); index=i; }
                else if (i < 32) { fn=(d&bb)|(~d&c); index=(5*i+1)%16; }
                else if (i < 48) { fn=bb^c^d; index=(3*i+5)%16; }
                else { fn=c^(bb|~d); index=(7*i)%16; }
                uint32_t next = bb + rol(a+fn+md5k[i]+w[index], shifts[(i/16)*4+i%4]);
                a=d; d=c; c=bb; bb=next;
            }
        }
        h[0]+=a; h[1]+=bb; h[2]+=c; h[3]+=d;
        if (sha256) { h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=hh; }
    }
    for (int i = 0; i < (sha256 ? 8 : 4); i++) put32(out+i*4, h[i], sha256);
}

void tea_block(unsigned char block[8], const uint32_t key[4], int decrypt, int be)
{
    uint32_t l=get32(block,be), r=get32(block+4,be), sum=decrypt ? 0xc6ef3720u : 0;
    for (int i=0; i<32; i++) {
        if (decrypt) {
            r -= ((l<<4)^(l>>5)) + (l^sum) + key[(sum>>11)&3];
            sum -= 0x9e3779b9u;
            l -= ((r<<4)^(r>>5)) + (r^sum) + key[sum&3];
        } else {
            l += ((r<<4)^(r>>5)) + (r^sum) + key[sum&3];
            sum += 0x9e3779b9u;
            r += ((l<<4)^(l>>5)) + (l^sum) + key[(sum>>11)&3];
        }
    }
    put32(block,l,be); put32(block+4,r,be);
}

#include "des_tables.h"

static uint64_t permute(uint64_t x, const unsigned char *table, int n, int bits)
{
    uint64_t out=0;
    for (int i=0; i<n; i++) out=(out<<1)|((x>>(bits-table[i]))&1);
    return out;
}

void algorithm_init(Algorithm *a, const unsigned char key[48], int tea)
{
    a->tea=tea;
    a->iv_length=0;
    memset(a->iv,0,sizeof(a->iv));
    if (tea) {
        for (int i=0; i<12; i++) a->keys[i/4][i%4]=get32(key+i*4,1);
    } else for (int i=0; i<6; i++) {
        uint64_t k=(uint64_t)get32(key+i*8,1)<<32|get32(key+i*8+4,1);
        k=permute(k,des_pc1,56,64);
        uint32_t l=(uint32_t)(k>>28), r=(uint32_t)(k&0xfffffff);
        for (int j=0; j<16; j++) {
            unsigned s=des_shifts[j];
            l=((l<<s)|(l>>(28-s)))&0xfffffff;
            r=((r<<s)|(r>>(28-s)))&0xfffffff;
            a->deskeys[i][j]=permute((uint64_t)l<<28|r,des_pc2,48,56);
        }
    }
}

static void des_block(unsigned char block[8], const uint64_t keys[16], int reverse)
{
    uint64_t v=(uint64_t)get32(block,1)<<32|get32(block+4,1);
    v=permute(v,des_ip,64,64);
    uint32_t l=(uint32_t)(v>>32), r=(uint32_t)v;
    for (int i=0; i<16; i++) {
        uint64_t x=permute(r,des_expansion,48,32)^keys[reverse ? 15-i : i];
        uint32_t s=0;
        for (int j=0; j<8; j++) {
            unsigned z=(unsigned)(x>>(42-j*6))&63;
            s=(s<<4)|des_sbox[j*64+(((z>>4)&2)|(z&1))*16+((z>>1)&15)];
        }
        uint32_t next=l^(uint32_t)permute(s,des_p,32,32);
        l=r; r=next;
    }
    v=permute((uint64_t)r<<32|l,des_fp,64,64);
    put32(block,(uint32_t)(v>>32),1); put32(block+4,(uint32_t)v,1);
}

static void transform(const Algorithm *a, unsigned char *raw, size_t n, int decrypt)
{
    static const int order[2][6]={{3,4,5,0,1,2},{2,1,0,5,4,3}};
    if (a->tea) {
        for (size_t i=0; i<n; i+=8)
            for (int j=0; j<3; j++) tea_block(raw+i,a->keys[decrypt?2-j:j],decrypt,1);
        return;
    }
    // The CBC template applies two full 3DES-CBC passes, each with the
    // same initial IV. Chaining around all six DES operations is different.
    for (int stage=0; stage<2; stage++) {
        unsigned char previous[8], saved[8]={0};
        memcpy(previous,a->iv,8);
        for (size_t i=0; i<n; i+=8) {
            if (a->iv_length) {
                if (decrypt) memcpy(saved,raw+i,8);
                else for (int k=0; k<8; k++) raw[i+k]^=previous[k];
            }
            for (int j=stage*3; j<stage*3+3; j++)
                des_block(raw+i,a->deskeys[order[decrypt][j]],decrypt^(j%3==1));
            if (a->iv_length) {
                if (decrypt) {
                    for (int k=0; k<8; k++) raw[i+k]^=previous[k];
                    memcpy(previous,saved,8);
                } else memcpy(previous,raw+i,8);
            }
        }
        wipe(previous,sizeof(previous)); wipe(saved,sizeof(saved));
    }
}

char *encrypt_xml(const Algorithm *a, const char *xml)
{
    size_t len=strlen(xml), n=(len/8+1)*8;
    if (len>MAX_XML) { fail("Request XML too large"); return NULL; }
    unsigned char *raw=calloc(1,n);
    char *out=malloc(n*2+1);
    if (!raw || !out) { free(raw); free(out); fail("Out of memory"); return NULL; }
    memcpy(raw,xml,len);
    transform(a,raw,n,0); hex_encode(raw,n,out,1);
    wipe(raw,n); free(raw);
    return out;
}

static int unhex(unsigned char c)
{
    if (c>='0' && c<='9') return c-'0';
    if (c>='a' && c<='f') return c-'a'+10;
    if (c>='A' && c<='F') return c-'A'+10;
    return -1;
}

char *decrypt_xml(const Algorithm *a, const char *body, size_t n)
{
    if (!n || n>2*(MAX_XML+8) || n%16) { fail("Invalid encrypted XML length"); return NULL; }
    size_t allocated=n/2+1;
    unsigned char *raw=calloc(1,allocated);
    if (!raw) { fail("Out of memory"); return NULL; }
    for (size_t i=0; i<n; i+=2) {
        int l=unhex((unsigned char)body[i]), r=unhex((unsigned char)body[i+1]);
        if (l<0 || r<0) goto invalid;
        raw[i/2]=(unsigned char)(l*16+r);
    }
    n/=2;
    transform(a,raw,n,1);
    unsigned char *zero=memchr(raw,0,n);
    if (!zero || (size_t)(raw+n-zero)>8) goto invalid;
    for (unsigned char *p=zero; p<raw+n; p++) if (*p) goto invalid;
    raw[n]=0;
    return (char *)raw;
invalid:
    wipe(raw,allocated); free(raw); fail("Invalid hexadecimal body or NUL padding"); return NULL;
}

typedef struct { char name[9]; uint32_t rva, raw, size; } Section;
typedef struct { const unsigned char *data; Section sections[32]; unsigned count; } PE;

static int pe_parse(PE *pe, const unsigned char *image, size_t n)
{
    if (n<64 || memcmp(image,"MZ",2)) return -1;
    size_t p=get32(image+60,0);
    if (p>n || n-p<24 || memcmp(image+p,"PE\0\0",4) || image[p+4]!=0x64 || image[p+5]!=0x86) return -1;
    pe->count=image[p+6]|(unsigned)image[p+7]<<8;
    size_t offset=p+24+(image[p+20]|(unsigned)image[p+21]<<8);
    if (!pe->count || pe->count>32 || offset>n || pe->count>(n-offset)/40) return -1;
    pe->data=image;
    for (unsigned i=0; i<pe->count; i++) {
        const unsigned char *s=image+offset+i*40;
        Section *section=&pe->sections[i];
        memcpy(section->name,s,8); section->name[8]=0;
        section->rva=get32(s+12,0); section->size=get32(s+16,0); section->raw=get32(s+20,0);
        if (section->raw>n || section->size>n-section->raw) return -1;
        for (unsigned j=0; j<i; j++) if (!strcmp(section->name,pe->sections[j].name)) return -1;
    }
    return 0;
}

static const unsigned char *pe_read(const PE *pe, uint32_t rva, size_t n)
{
    for (unsigned i=0; i<pe->count; i++) {
        const Section *s=&pe->sections[i];
        if (rva>=s->rva && rva-s->rva<=s->size && n<=s->size-(rva-s->rva))
            return pe->data+s->raw+(rva-s->rva);
    }
    return NULL;
}

static int load_image(Algorithm *a, const unsigned char *image, size_t n)
{
    static const char *hashes[3][2]={
        {"888c189c7d9ab436a124711524735b6c7ee810678bfa712a59777f1be6d549d1",
         "64764eb5c54a91844d6271f611ec84fe9a39ce8bccfa0ae528274d3db826bd58"},
        {"61c7ced307a0c1674a302cd986013720282e4dcdeb04f883e609be2a6e1aa41a",
         "aa38c3f635d92580f82a7b2f58e98181c80cb1518e7cef09870fa4081085869d"},
        {"ed61dfa149ee837b8c0a061ed09b9ca0be784d0cd1882e978f768ff1f3225c03",
         "64567d9aaeb7aebe0a1f06d1c398a680894395158e06a00d56838528b775bfcd"}
    };
    PE pe;
    if (pe_parse(&pe,image,n)) return fail("Invalid algorithm PE image");
    char actual[2][65]={{0}};
    for (unsigned i=0; i<pe.count; i++) {
        Section *s=&pe.sections[i];
        int index=!strcmp(s->name,".text") ? 0 : !strcmp(s->name,".rdata") ? 1 : -1;
        if (index>=0) { unsigned char h[32]; digest(image+s->raw,s->size,h,1); hex_encode(h,32,actual[index],0); }
    }
    int template;
    for (template=0; template<3; template++)
        if (!strcmp(actual[0],hashes[template][0]) && !strcmp(actual[1],hashes[template][1])) break;
    if (template==3) return fail("Unsupported algorithm template");
    int tea=template==1;
    if (!tea) {
        static const struct { uint32_t rva; const unsigned char *table; size_t n; } tables[]={
            {0x11100,des_pc1,sizeof(des_pc1)}, {0x11040,des_pc2,sizeof(des_pc2)},
            {0x113a0,des_ip,sizeof(des_ip)}, {0x111e0,des_fp,sizeof(des_fp)},
            {0x112e0,des_expansion,sizeof(des_expansion)}, {0x114a0,des_p,sizeof(des_p)},
            {0xe170,des_shifts,sizeof(des_shifts)}, {0xd970,des_sbox,sizeof(des_sbox)}
        };
        for (size_t i=0; i<sizeof(tables)/sizeof(tables[0]); i++) {
            const unsigned char *p=pe_read(&pe,tables[i].rva,tables[i].n*4);
            if (!p) return fail("Missing DES tables");
            for (size_t j=0; j<tables[i].n; j++) if (get32(p+j*4,0)!=tables[i].table[j]) return fail("Unsupported DES tables");
        }
    }
    const unsigned char *meta=pe_read(&pe,tea?0xf1d9:0x116b9,6);
    if (!meta || (meta[0] && (template!=2 || meta[0]!=8)) ||
        meta[2]!=48 || meta[5]+48+meta[0]+7>256) return fail("Unsupported algorithm key layout");
    const unsigned char *key=pe_read(&pe,(tea?0xf0e0:0x115c0)+meta[5],48+meta[0]);
    if (!key) return fail("Algorithm key outside image");
    algorithm_init(a,key,tea);
    a->iv_length=meta[0];
    if (a->iv_length) memcpy(a->iv,key+48,8);
    return 0;
}

int algorithm_load(Algorithm *a, const unsigned char *package, size_t n)
{
    static const uint32_t keys[2][4]={{0x464b697c,0x463b6d4d,0x36496a5a,0x4774496f},
                                    {0x356d766d,0x52783633,0x2d4a4d38,0x39384335}};
    /* Known templates use the exact fixed-width metadata envelope. */
    if (n<=114 || n>MAX_PACKAGE || memcmp(package,"001",3) || package[3]!=64 || package[68]!=36)
        return fail("Unsupported AutoZSM envelope");
    for (int i=4; i<68; i++) if (unhex(package[i])<0) return fail("Invalid AutoZSM metadata");
    Algorithm next={0};
    memcpy(next.id,package+69,36);
    if (!valid_uuid(next.id)) return fail("Invalid algorithm UUID");
    for (int i=0; i<36; i++) next.id[i]=(char)tolower((unsigned char)next.id[i]);
    uint32_t packed=get32(package+110,0), size=packed&0x0fffffff;
    if ((packed>>28)!=2 || !size || size>MAX_PACKAGE || (n-114)%8) return fail("Invalid AutoZSM type/size");
    unsigned prop=package[105], dict=get32(package+106,0);
    lzma_options_lzma options={0};
    options.lc=prop%9; options.lp=(prop/9)%5; options.pb=prop/45;
    if (prop>=225 || options.lc+options.lp>4 || dict<4096 || dict>16*1024*1024) return fail("Unsupported LZMA properties");
    /* No preset dictionary: backward references cannot precede byte zero.
     * A size-delimited 64 KiB image never needs the advertised 16 MiB dictionary. */
    options.dict_size=dict<size ? dict : size;
    if (options.dict_size<4096) options.dict_size=4096;
    size_t compressed_size=n-114;
    unsigned char *compressed=malloc(compressed_size), *image=calloc(1,size);
    if (!compressed || !image) { free(compressed); free(image); return fail("Out of memory"); }
    memcpy(compressed,package+114,compressed_size);
    for (size_t i=0; i<compressed_size; i+=8) {
        tea_block(compressed+i,keys[0],1,0); tea_block(compressed+i,keys[1],1,0);
    }
    int result=-1;
    unsigned pad=compressed[compressed_size-1];
    if (!pad || pad>8) { fail("Invalid AutoZSM padding"); goto done; }
    lzma_filter filters[]={{LZMA_FILTER_LZMA1,&options},{LZMA_VLI_UNKNOWN,NULL}};
    lzma_stream stream=LZMA_STREAM_INIT;
    lzma_ret ret=lzma_raw_decoder(&stream,filters);
    if (ret!=LZMA_OK) { lzma_end(&stream); fail("Cannot initialize LZMA decoder"); goto done; }
    stream.next_in=compressed; stream.avail_in=compressed_size-pad;
    stream.next_out=image; stream.avail_out=size;
    do {
        size_t before_in=stream.avail_in, before_out=stream.avail_out;
        ret=lzma_code(&stream,LZMA_RUN);
        if (ret!=LZMA_OK || (before_in==stream.avail_in && before_out==stream.avail_out)) break;
    } while (stream.avail_out);
    int complete=!stream.avail_out && (ret==LZMA_OK || ret==LZMA_STREAM_END);
    lzma_end(&stream);
    if (!complete) { fail("Algorithm decompression failed or size mismatch"); goto done; }
    result=load_image(&next,image,size);
    if (!result) { wipe(a,sizeof(*a)); *a=next; }
done:
    wipe(&next,sizeof(next)); wipe(image,size); wipe(compressed,compressed_size);
    free(image); free(compressed);
    return result;
}

// SPDX-License-Identifier: MIT
#include "../core/auth.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void make_page(uint8_t out[63], uint8_t id, uint8_t page, const uint8_t nonce[280]) {
    uint8_t full[64] = {0}; full[0] = 0xf0; full[1] = id; full[2] = page;
    memcpy(full+4, nonce+56*page, page == 4 ? 32 : 56);
    mp_put32(full+60, mp_crc32(full, 60)); memcpy(out, full+1, 63);
}
static void protocol_roundtrip(void) {
    mp_packet a = {.kind=MP_CHALLENGE,.session=0x12345678,.token=0x9abcdef0,.length=280};
    for (unsigned i=0;i<a.length;i++) a.payload[i]=(uint8_t)i;
    uint8_t wire[MP_FRAME_MAX]; size_t n=mp_encode(&a,wire,sizeof(wire)); assert(n>0 && wire[n-1]==0);
    mp_packet b; assert(mp_decode(wire,n-1,&b)); assert(b.kind==a.kind && b.session==a.session && b.token==a.token && b.length==a.length && !memcmp(a.payload,b.payload,a.length));
    wire[5]^=1; assert(!mp_decode(wire,n-1,&b));
    mp_parser parser={0}; bool decoded=false;
    for(size_t i=0;i<n;i++) decoded = mp_feed(&parser,wire[i],&b) || decoded;
    assert(!decoded && parser.errors==1);
    assert(mp_feed(&parser, 0, &b) == false); // empty delimiters are ignored
}
static void auth_sequence(void) {
    auth_state s={0}; auth_reset(&s); uint8_t nonce[280], page[63], response[1065], out[64];
    for(unsigned i=0;i<sizeof(nonce);i++) nonce[i]=(uint8_t)(i*3+1);
    for(unsigned i=0;i<5;i++) { make_page(page, 7, (uint8_t)i, nonce); assert(auth_set_page(&s,page,63,100)); }
    assert(s.phase==AUTH_WAITING && s.bitmap==31);
    uint32_t generation=s.generation;
    response[0]=7; for(unsigned i=1;i<sizeof(response);i++) response[i]=(uint8_t)(i^0xa5);
    assert(auth_supply(&s,generation,response,sizeof(response)));
    assert(s.phase==AUTH_READY);
    for(unsigned i=0;i<19;i++) { size_t n=auth_get(&s,0xf1,out,sizeof(out)); assert(n==63 && out[0]==7 && out[1]==i); }
    assert(s.phase==AUTH_SERVED); assert(auth_get(&s,0xf1,out,sizeof(out))==63);
    assert(auth_get(&s,0xf2,out,sizeof(out))==15 && out[2]==0);
    uint8_t stale[1065]; stale[0]=7; memset(stale+1, 1, 1064); auth_reset(&s); assert(!auth_supply(&s,generation,stale,sizeof(stale)));
    make_page(page, 7, 0, nonce); page[8]^=0x40; assert(!auth_set_page(&s,page,63,200));
}
int main(void) { protocol_roundtrip(); auth_sequence(); puts("core tests passed"); return 0; }

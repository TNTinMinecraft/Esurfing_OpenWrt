CC ?= cc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -Os
CPPFLAGS += -Isrc $(shell $(PKG_CONFIG) --cflags libcurl liblzma expat)
CFLAGS += -std=c99 -Wall -Wextra -Wpedantic
LDLIBS += $(shell $(PKG_CONFIG) --libs libcurl liblzma expat)
SOURCES = src/network.c src/main.c src/protocol.c src/crypto.c

all: esurfing

esurfing: $(SOURCES) src/esurfing.h src/des_tables.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $(SOURCES) $(LDLIBS)

clean:
	rm -f esurfing

.PHONY: all clean

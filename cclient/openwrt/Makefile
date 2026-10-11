include $(TOPDIR)/rules.mk

PKG_NAME:=esurfing-c
PKG_VERSION:=0.2.0
PKG_RELEASE:=2
PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

include $(INCLUDE_DIR)/package.mk

define Package/esurfing-c
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Minimal Jiangsu campus portal client
  DEPENDS:=+libcurl +liblzma +libexpat +libgcc
endef

define Package/esurfing-c/description
  Foreground C client for the observed Jiangsu campus HTTP portal.
  Discovers the portal, authenticates, sends keepalives and reconnects after outages.
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) $(CURDIR)/src/. $(PKG_BUILD_DIR)/
endef

define Build/Configure
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c99 -Wall -Wextra \
		-ffunction-sections -fdata-sections -I$(PKG_BUILD_DIR) \
		$(PKG_BUILD_DIR)/main.c $(PKG_BUILD_DIR)/network.c $(PKG_BUILD_DIR)/protocol.c $(PKG_BUILD_DIR)/crypto.c \
		$(TARGET_LDFLAGS) -Wl,--gc-sections -lcurl -llzma -lexpat \
		-o $(PKG_BUILD_DIR)/esurfing
endef

define Package/esurfing-c/conffiles
/etc/config/esurfing
/etc/esurfing/credentials
endef

define Package/esurfing-c/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/esurfing $(1)/usr/bin/esurfing
	$(INSTALL_DIR) $(1)/etc/init.d $(1)/etc/config $(1)/etc/esurfing
	chmod 700 $(1)/etc/esurfing
	$(INSTALL_BIN) ./files/esurfing.init $(1)/etc/init.d/esurfing
	$(INSTALL_CONF) ./files/esurfing.config $(1)/etc/config/esurfing
	$(INSTALL_DATA) ./files/credentials.example $(1)/etc/esurfing/credentials.example
endef

$(eval $(call BuildPackage,esurfing-c))

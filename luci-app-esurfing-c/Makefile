include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-esurfing-c
PKG_VERSION:=0.2.0
PKG_RELEASE:=3

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-esurfing-c
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=LuCI interface for the esurfing C client
  PKGARCH:=all
  DEPENDS:=+esurfing-c +luci-base +rpcd-mod-ucode +ucode-mod-fs +ucode-mod-uci +ucode-mod-ubus
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/luci-app-esurfing-c/install
	$(INSTALL_DIR) $(1)/www $(1)/usr
	$(CP) ./htdocs/. $(1)/www/
	$(CP) ./root/usr/. $(1)/usr/
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./refresh-luci.sh $(1)/etc/uci-defaults/90-luci-esurfing
endef

define Package/luci-app-esurfing-c/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	/etc/uci-defaults/90-luci-esurfing && rm -f /etc/uci-defaults/90-luci-esurfing
}
exit 0
endef

define Package/luci-app-esurfing-c/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	rm -f /tmp/luci-indexcache.*
	rm -rf /tmp/luci-modulecache/
	/etc/init.d/rpcd reload 2>/dev/null || :
}
exit 0
endef

$(eval $(call BuildPackage,luci-app-esurfing-c))

#!/bin/sh
# Safe both after a live install and on the first boot of an image build.
rm -f /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache/
/etc/init.d/rpcd reload 2>/dev/null || :
exit 0

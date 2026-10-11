#!/usr/bin/env bash
# Build unsigned packages with an unpacked Linux OpenWrt APK SDK. No test targets.
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 /path/to/openwrt-sdk /path/to/empty-output-directory" >&2
    exit 2
fi
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sdk_dir=$(cd -- "$1" && pwd)
[[ -f "$sdk_dir/include/package.mk" ]] || { echo "Not an OpenWrt SDK" >&2; exit 2; }
mkdir -p -- "$2"
output_dir=$(cd -- "$2" && pwd)
[[ -z $(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit) ]] || {
    echo "Output directory must be empty: $output_dir" >&2
    exit 2
}
jobs=${JOBS:-2}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "JOBS must be a positive integer" >&2; exit 2; }

cd "$sdk_dir"
# Keep the feeds/revisions shipped by the chosen SDK; do not switch to master.
./scripts/feeds update -a
./scripts/feeds install -a
mkdir -p package/esurfing-c/src package/esurfing-c/files package/luci-app-esurfing-c
cp "$repo_dir/cclient/openwrt/Makefile" package/esurfing-c/Makefile
cp "$repo_dir"/cclient/src/* package/esurfing-c/src/
cp "$repo_dir"/cclient/openwrt/files/* package/esurfing-c/files/
cp "$repo_dir/luci-app-esurfing-c/Makefile" package/luci-app-esurfing-c/
cp "$repo_dir/luci-app-esurfing-c/refresh-luci.sh" package/luci-app-esurfing-c/
cp -R "$repo_dir/luci-app-esurfing-c/htdocs" "$repo_dir/luci-app-esurfing-c/root" package/luci-app-esurfing-c/

touch .config
for option in ALL ALL_NONSHARED ALL_KMODS SIGNED_PACKAGES USE_APK PACKAGE_esurfing-c PACKAGE_luci-app-esurfing-c; do
    sed -i -e "/^CONFIG_${option}=/d" -e "/^# CONFIG_${option} is not set$/d" .config
done
cat >> .config <<'CONFIG'
# CONFIG_ALL is not set
# CONFIG_ALL_NONSHARED is not set
# CONFIG_ALL_KMODS is not set
# CONFIG_SIGNED_PACKAGES is not set
CONFIG_USE_APK=y
CONFIG_PACKAGE_esurfing-c=m
CONFIG_PACKAGE_luci-app-esurfing-c=m
CONFIG
make defconfig
grep -qx 'CONFIG_USE_APK=y' .config || { echo "SDK does not support APK" >&2; exit 1; }
# Fail before compilation if a matrix entry is paired with the wrong SDK/ABI.
package_arch=$(sed -n 's/^CONFIG_TARGET_ARCH_PACKAGES="\([^"]*\)"$/\1/p' .config)
target_board=$(sed -n 's/^CONFIG_TARGET_BOARD="\([^"]*\)"$/\1/p' .config)
target_subtarget=$(sed -n 's/^CONFIG_TARGET_SUBTARGET="\([^"]*\)"$/\1/p' .config)
[[ -n "$package_arch" && -n "$target_board" && -n "$target_subtarget" ]] || {
    echo "SDK target identity is missing" >&2
    exit 1
}
if [[ "$package_arch" != "${EXPECTED_PACKAGE_ARCH:-$package_arch}" ||
      "$target_board" != "${EXPECTED_TARGET_BOARD:-$target_board}" ||
      "$target_subtarget" != "${EXPECTED_TARGET_SUBTARGET:-$target_subtarget}" ]]; then
    echo "Unexpected SDK target: $target_board/$target_subtarget ($package_arch)" >&2
    exit 1
fi
for package in esurfing-c luci-app-esurfing-c; do
    grep -Eq "^CONFIG_PACKAGE_${package}=[my]$" .config || {
        echo "Package was not selected: $package" >&2
        exit 1
    }
done

# Regenerate the two packages so a reused SDK cannot export previously signed
# copies. OpenWrt 25.12's targeted package builds produce unsigned APKs.
make package/esurfing-c/clean package/luci-app-esurfing-c/clean
make -j"$jobs" package/esurfing-c/compile V=s
make -j"$jobs" package/luci-app-esurfing-c/compile V=s

for name in esurfing-c luci-app-esurfing-c; do
    mapfile -d '' packages < <(find bin/packages -type f -name "$name-*.apk" -print0)
    [[ ${#packages[@]} -eq 1 ]] || {
        echo "Expected one $name APK, found ${#packages[@]}; use a fresh SDK." >&2
        exit 1
    }
    package=${packages[0]}
    cp "$package" "$output_dir/"
done
cp "$repo_dir/docs/install.md" "$output_dir/INSTALL.md"
cp "$repo_dir/LICENSE" "$output_dir/LICENSE"
{
    printf 'Source commit: %s\n' "$(git -C "$repo_dir" rev-parse HEAD)"
    printf 'Build time (UTC): %s\n' "$(date -u +%FT%TZ)"
    printf 'SDK archive: %s\n' "${SDK_ARCHIVE:-locally supplied SDK}"
    printf 'SDK SHA256: %s\n' "${SDK_SHA256:-not supplied}"
    printf 'Package architecture: %s\n' "$package_arch"
    printf 'OpenWrt target: %s/%s\n' "$target_board" "$target_subtarget"
    printf 'Package signing: unsigned (install with apk add --allow-untrusted)\n'
    printf '\nSDK target configuration:\n'
    grep -E '^CONFIG_TARGET_(BOARD|SUBTARGET|ARCH_PACKAGES)=' .config
    printf '\nPackage versions:\n'
    grep -E '^PKG_(NAME|VERSION|RELEASE):=' package/{esurfing-c,luci-app-esurfing-c}/Makefile
    printf '\nFeed revisions:\n'
    for feed in feeds/*; do
        [[ -d "$feed/.git" || -f "$feed/.git" ]] || continue
        printf '%s %s\n' "$(basename "$feed")" "$(git -C "$feed" rev-parse HEAD)"
    done
    printf '\nNo project tests were run. Dependencies are supplied by the router firmware feeds.\n'
} > "$output_dir/BUILD-INFO.txt"
(
    cd "$output_dir"
    sha256sum -- *.apk INSTALL.md LICENSE BUILD-INFO.txt > SHA256SUMS
)
printf 'Release files: %s\n' "$output_dir"

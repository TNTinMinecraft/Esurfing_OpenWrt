#!/usr/bin/env bash
# Workflow build API: 3
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
jobs=${JOBS:-$(nproc)}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "JOBS must be a positive integer" >&2; exit 2; }

build_luci=${BUILD_LUCI:-true}
case "$build_luci" in
    true|false) ;;
    *) echo "BUILD_LUCI must be true or false" >&2; exit 2 ;;
esac
package_names=(esurfing-c)
feed_names=(base packages)
feed_packages=(libcurl liblzma libexpat libgcc)
build_target=esurfing-c
if [[ "$build_luci" == true ]]; then
    package_names+=(luci-app-esurfing-c)
    feed_names+=(luci)
    feed_packages+=(luci-base rpcd-mod-ucode ucode-mod-fs ucode-mod-uci ucode-mod-ubus)
    build_target=luci-app-esurfing-c
fi

cd "$sdk_dir"
# An exact CI cache contains the SDK, pinned feeds and compiled dependencies.
# Local builds refresh the required feeds unless explicitly reusing that cache.
if [[ ${REUSE_SDK:-false} == true ]]; then
    [[ -n ${SDK_SHA256:-} && -f .esurfing-sdk-ready &&
       $(cat .esurfing-sdk-ready) == "$SDK_SHA256 luci=$build_luci" ]] || {
        echo "SDK cache marker mismatch; remove the cache and rebuild." >&2
        exit 1
    }
    echo 'Reusing SDK feeds and compiled dependencies.'
else
    # Keep the revisions shipped by the SDK; routing/telephony/video are unused.
    ./scripts/feeds update "${feed_names[@]}"
    # feeds install recursively installs runtime and build dependency recipes.
    ./scripts/feeds install "${feed_packages[@]}"
fi
# Replace project files so deleted source/assets cannot survive a cached SDK.
rm -rf package/esurfing-c package/luci-app-esurfing-c
mkdir -p package/esurfing-c/src package/esurfing-c/files
cp "$repo_dir/cclient/openwrt/Makefile" package/esurfing-c/Makefile
cp "$repo_dir"/cclient/src/* package/esurfing-c/src/
cp "$repo_dir"/cclient/openwrt/files/* package/esurfing-c/files/
if [[ "$build_luci" == true ]]; then
    mkdir -p package/luci-app-esurfing-c
    cp "$repo_dir/luci-app-esurfing-c/Makefile" package/luci-app-esurfing-c/
    cp "$repo_dir/luci-app-esurfing-c/refresh-luci.sh" package/luci-app-esurfing-c/
    cp -R "$repo_dir/luci-app-esurfing-c/htdocs" "$repo_dir/luci-app-esurfing-c/root" package/luci-app-esurfing-c/
fi

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
CONFIG
if [[ "$build_luci" == true ]]; then
    echo 'CONFIG_PACKAGE_luci-app-esurfing-c=m' >> .config
else
    echo '# CONFIG_PACKAGE_luci-app-esurfing-c is not set' >> .config
fi
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
for package in "${package_names[@]}"; do
    grep -Eq "^CONFIG_PACKAGE_${package}=[my]$" .config || {
        echo "Package was not selected: $package" >&2
        exit 1
    }
done

# Regenerate the two packages so a reused SDK cannot export previously signed
# copies. OpenWrt 25.12's targeted package builds produce unsigned APKs.
for package in "${package_names[@]}"; do
    make "package/$package/clean"
done
# Remove any older package versions left by a reused local SDK as well.
if [[ -d bin/packages ]]; then
    find bin/packages -type f \( -name 'esurfing-c-*.apk' -o -name 'luci-app-esurfing-c-*.apk' \) -delete
fi
# With LuCI enabled, its dependency on esurfing-c builds both in one traversal.
make -j"$jobs" "package/$build_target/compile" V=s

for name in "${package_names[@]}"; do
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
    printf 'SDK cache reused: %s\n' "${REUSE_SDK:-false}"
    printf 'Parallel jobs: %s\n' "$jobs"
    printf 'Build LuCI (noarch): %s\n' "$build_luci"
    printf 'Package architecture: %s\n' "$package_arch"
    printf 'OpenWrt target: %s/%s\n' "$target_board" "$target_subtarget"
    printf 'Package signing: unsigned (install with apk add --allow-untrusted)\n'
    printf '\nSDK target configuration:\n'
    grep -E '^CONFIG_TARGET_(BOARD|SUBTARGET|ARCH_PACKAGES)=' .config
    printf '\nPackage versions:\n'
    for package in "${package_names[@]}"; do
        grep -E '^PKG_(NAME|VERSION|RELEASE):=' "package/$package/Makefile"
    done
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
if [[ -n ${SDK_SHA256:-} ]]; then
    printf '%s luci=%s\n' "$SDK_SHA256" "$build_luci" > .esurfing-sdk-ready
fi
printf 'Release files: %s\n' "$output_dir"

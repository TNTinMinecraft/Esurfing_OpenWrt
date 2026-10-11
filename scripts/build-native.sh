#!/usr/bin/env bash
# Native macOS/Linux release binaries; no tests or authentication are run.
# Keep Bash 3.2 compatibility for the macOS runner.
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 macos|linux arm64|x86_64 /path/to/empty-output-directory" >&2
    exit 2
fi
platform=$1
arch=$2
case "$platform:$arch" in
    macos:arm64|macos:x86_64|linux:arm64|linux:x86_64) ;;
    *) echo "Unsupported platform/architecture: $platform/$arch" >&2; exit 2 ;;
esac
case "$(uname -s)" in
    Darwin) host_platform=macos ;;
    Linux) host_platform=linux ;;
    *) echo 'Native builds require macOS or Linux' >&2; exit 2 ;;
esac
case "$(uname -m)" in
    arm64|aarch64) host_arch=arm64 ;;
    x86_64) host_arch=x86_64 ;;
    *) echo 'Unsupported runner CPU architecture' >&2; exit 2 ;;
esac
[[ "$platform:$arch" == "$host_platform:$host_arch" ]] || {
    echo "Requested $platform/$arch but runner is $host_platform/$host_arch" >&2
    exit 2
}
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p -- "$3"
output_dir=$(cd -- "$3" && pwd)
[[ -z $(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit) ]] || {
    echo "Output directory must be empty: $output_dir" >&2
    exit 2
}

if [[ "$platform" == macos ]]; then
    # Select the three shared libraries explicitly, including keg-only formulas.
    # Their indirect dependencies are loaded by dyld at runtime.
    curl_prefix=$(brew --prefix curl)
    xz_prefix=$(brew --prefix xz)
    expat_prefix=$(brew --prefix expat)
    export MACOSX_DEPLOYMENT_TARGET=15.0
    compiler=$(xcrun --find clang)
    build_args=("CC=$compiler"
        "CPPFLAGS=-Isrc -I$curl_prefix/include -I$xz_prefix/include -I$expat_prefix/include"
        "LDLIBS=$curl_prefix/lib/libcurl.dylib $xz_prefix/lib/liblzma.dylib $expat_prefix/lib/libexpat.dylib")
else
    compiler=/usr/bin/gcc
    pkg_config=/usr/bin/pkg-config
    export PKG_CONFIG_LIBDIR="/usr/lib/$($compiler -dumpmachine)/pkgconfig:/usr/share/pkgconfig"
    export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
    "$pkg_config" --exists libcurl liblzma expat
    build_args=("CC=$compiler" "PKG_CONFIG=$pkg_config")
fi
make -C "$repo_dir/cclient" clean
make -C "$repo_dir/cclient" "${build_args[@]}"
install -m 755 "$repo_dir/cclient/esurfing" "$output_dir/esurfing"
cp "$repo_dir/docs/native.md" "$output_dir/NATIVE.md"
cp "$repo_dir/LICENSE" "$output_dir/LICENSE"

# Inspect executable metadata only; do not run the newly built client.
{
    printf 'Source commit: %s\n' "$(git -C "$repo_dir" rev-parse HEAD)"
    printf 'Build time (UTC): %s\n' "$(date -u +%FT%TZ)"
    printf 'Platform: %s\nArchitecture: %s\nLinkage: dynamic\n' "$platform" "$arch"
    printf '\nCompiler:\n'
    "$compiler" --version
    printf '\nBuild dependency versions:\n'
    if [[ "$platform" == macos ]]; then
        brew list --versions curl xz expat
    else
        for dependency in libcurl liblzma expat; do
            printf '%s: %s\n' "$dependency" "$("$pkg_config" --modversion "$dependency")"
        done
    fi
    printf '\nExecutable format:\n'
    file "$output_dir/esurfing"
    if [[ "$platform" == macos ]]; then
        printf '\nmacOS deployment target: %s\n' "$MACOSX_DEPLOYMENT_TARGET"
        sw_vers
        printf '\nDynamic library paths:\n'
        otool -L "$output_dir/esurfing"
    else
        printf '\nBuild distribution:\n'
        cat /etc/os-release
        printf '\nELF headers and runtime dependencies:\n'
        readelf -h -d --version-info "$output_dir/esurfing"
    fi
    printf '\nNo project tests or live authentication were run.\n'
} > "$output_dir/BUILD-INFO.txt"
(
    cd "$output_dir"
    if [[ "$platform" == macos ]]; then
        shasum -a 256 esurfing NATIVE.md LICENSE BUILD-INFO.txt > SHA256SUMS
    else
        sha256sum esurfing NATIVE.md LICENSE BUILD-INFO.txt > SHA256SUMS
    fi
)
printf 'Native release files: %s\n' "$output_dir"

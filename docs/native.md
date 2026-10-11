# macOS / Linux 可执行文件

GitHub Release 同时提供四份 C 客户端可执行文件压缩包。ARM 和 x86 在这些附件中分别指 **ARM64** 与 **x86_64**，不是 32 位程序。

| Release 附件 | 目标系统 | 编译环境 |
| --- | --- | --- |
| `esurfing-macos-arm64.tar.gz` | Apple Silicon Mac，macOS 15 或更新版本 | `macos-15` 原生 ARM64 runner |
| `esurfing-macos-x86_64.tar.gz` | Intel Mac，macOS 15 或更新版本 | `macos-15-intel` 原生 x86_64 runner |
| `esurfing-linux-arm64.tar.gz` | ARM64 Linux，glibc 环境 | `ubuntu-22.04-arm` 原生 runner |
| `esurfing-linux-x86_64.tar.gz` | x86_64 Linux，glibc 环境 | `ubuntu-22.04` 原生 runner |

每份压缩包含真正的 ELF 或 Mach-O 可执行文件 `esurfing`，以及 `SHA256SUMS`、`BUILD-INFO.txt`、`NATIVE.md` 和 `LICENSE`。tar 保留可执行权限。这里只包含命令行客户端，UCI/procd/LuCI 组件随 OpenWrt APK 提供。

## 运行库

可执行文件采用动态链接，需要 libcurl、liblzma 和 libexpat。无需安装 C 编译器或 Python。

macOS 使用对应 CPU 架构的 Homebrew 安装运行库：

```sh
brew install curl xz expat
```

ARM64 构建链接 `/opt/homebrew` 下的库，Intel 构建链接 `/usr/local` 下的库；具体路径见压缩包中的 `BUILD-INFO.txt`。请使用匹配架构及默认前缀的 Homebrew，保持依赖已安装。构建未配置 Apple Developer ID 签名或公证，不需要签名 Secret。

Linux 二进制在 Ubuntu 22.04 上构建，面向 glibc 2.35 或更新且具备兼容动态库的系统。Ubuntu/Debian 上安装运行库，例如：

```sh
sudo apt-get update
sudo apt-get install libcurl4 liblzma5 libexpat1 ca-certificates
```

发行版可能对 libcurl 包重命名（例如 `libcurl4t64`）；以系统软件源提供 `libcurl.so.4` 的包为准。实际依赖的 SONAME 与符号版本记录在 `BUILD-INFO.txt`。这些不是静态通用 Linux 程序，Alpine/OpenWrt 等 musl 系统请使用对应构建。

## 解压与运行

从同一次 Release 下载匹配系统的压缩包和总 `SHA256SUMS`，核对该附件的摘要；解压到空目录后再校验内部文件：

```sh
# macOS
shasum -a 256 -c SHA256SUMS
# Linux
sha256sum -c SHA256SUMS
```

停止其他天翼客户端，选择接入校园网的实际网卡。Linux 通常需要 root 权限绑定接口：

```sh
sudo ./esurfing --interface eth1
```

macOS 需要显式提供校园网 IPv4 网关，例如：

```sh
./esurfing --interface en0 --gateway 192.0.2.1
```

把示例网卡及网关替换为实际值。程序交互询问账号密码；`Ctrl+C` 尝试注销。也可通过 `--credentials /absolute/path/credentials.conf` 使用权限为 `0600` 的两行 UTF-8 文件（账号、密码）。

本地程序解压后直接执行，不使用 APK 安装命令。协议、重试和已知限制与 C 客户端一致；工作流不会运行测试或进行实际校园网认证。

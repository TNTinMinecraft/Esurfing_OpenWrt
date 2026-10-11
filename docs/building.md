# 构建与手动发布

## GitHub Actions

工作流文件为 `.github/workflows/release-apk.yml`，仅声明 `workflow_dispatch`。推送源码、推送 tag 或提交 PR 都不会自动触发。OpenWrt 构建运行于 Ubuntu 24.04，使用 [OpenWrt 25.12.0 官方 SDK](https://downloads.openwrt.org/releases/25.12.0/targets/)。`strategy.matrix.include` 一次构建下表九个目标，每个 SDK 文件名和 SHA-256 均独立固定，libc 为 musl。

| CPU 类别 | OpenWrt 包架构 | 官方 SDK target/subtarget |
| --- | --- | --- |
| x86-64 | `x86_64` | [x86/64](https://downloads.openwrt.org/releases/25.12.0/targets/x86/64/) |
| ARMv7 | `arm_cortex-a9_vfpv3-d16` | [mvebu/cortexa9](https://downloads.openwrt.org/releases/25.12.0/targets/mvebu/cortexa9/) |
| ARM64 | `aarch64_generic` | [armsr/armv8](https://downloads.openwrt.org/releases/25.12.0/targets/armsr/armv8/) |
| ARM64 Cortex-A53 | `aarch64_cortex-a53` | [mediatek/filogic](https://downloads.openwrt.org/releases/25.12.0/targets/mediatek/filogic/) |
| MIPS 大端 | `mips_24kc` | [ath79/generic](https://downloads.openwrt.org/releases/25.12.0/targets/ath79/generic/) |
| MIPS 小端 | `mipsel_24kc` | [ramips/mt7621](https://downloads.openwrt.org/releases/25.12.0/targets/ramips/mt7621/) |
| MIPS64 大端 | `mips64_mips64r2` | [malta/be64](https://downloads.openwrt.org/releases/25.12.0/targets/malta/be64/) |
| MIPS64 小端 | `mips64el_mips64r2` | [malta/le64](https://downloads.openwrt.org/releases/25.12.0/targets/malta/le64/) |
| RISC-V 64 | `riscv64_generic` | [sifiveu/generic](https://downloads.openwrt.org/releases/25.12.0/targets/sifiveu/generic/) |

矩阵使用 C 交叉编译工具链和具体 OpenWrt ABI，不使用 Go 的 `GOOS` / `GOARCH` / `GOMIPS`。浮点 ABI、端序和 CPU 指令集遵循各 SDK；ARMv7、ARM64 或 MIPS64 名称相近并不表示包可通用。Malta 的 MIPS64 包也不能代替 Octeon 包。macOS 和普通 Linux 的可执行文件由下述独立矩阵构建。

`source` job 先将输入 tag 解析为固定提交，所有矩阵任务和发布文档均检出该提交。`fail-fast: false` 让单个架构失败时其他任务继续完成；只有九个 OpenWrt 目标和四个原生可执行文件目标全部成功才创建 Release，成功目标的 Artifact 在其他目标失败时仍可下载。

手动发布顺序：

1. 将本仓库源码提交并推送到 GitHub，确保工作流文件已进入默认分支；GitHub 要求默认分支存在手动工作流才显示运行入口。
2. 为要发布的提交创建并推送 tag，例如 `v0.2.0`。该提交必须包含当前构建脚本和文档，包括 macOS SDK 修复。输入旧 tag 不会使用默认分支上的新脚本；工作流会在解析源码时检查脚本版本，提前拒绝不兼容的旧 tag。
3. 在 **Actions → Build and release APKs and binaries → Run workflow** 选择默认分支（通常为 `main`），在输入框填写新 tag。默认构建后直接发布；勾选 `draft` 则生成 Release 草稿。

workflow 本身使用所选分支/版本中的定义，包源码始终从输入的 `refs/tags/<tag>` 解析出的固定提交检出。发布前会解析普通或 annotated tag 并核对构建提交，拒绝构建期间移动过的 tag。已有同名 Release 时发布失败，不覆盖现有附件；需要再次发布时使用新 tag，或自行处理已有 Release。

构建 job 只有仓库读取权限，Release job 才获得 `contents: write`，使用内置 `GITHUB_TOKEN`，不需要个人访问令牌。若仓库或组织限制 Actions 的写权限，需要允许该发布 job 创建 Release。

每个 OpenWrt 目标生成一个独立压缩包，命名为 `esurfing-openwrt-25.12.0-<target>-<subtarget>-<arch>.tar.gz`。这样无需改写 APK 内部架构，也不会因 APK、noarch LuCI 或清单同名而互相覆盖。只下载与设备匹配的压缩包，解压后包含：

- `esurfing-c-*.apk`：C 客户端、UCI 和 procd 服务。
- `luci-app-esurfing-c-*.apk`：架构无关的 LuCI 界面。
- `SHA256SUMS`、`BUILD-INFO.txt`、`INSTALL.md` 和 `LICENSE`。

Release 和各 OpenWrt Artifact 还直接提供两个独立 `.apk` 文件，文件名追加 `<target>-<subtarget>-<arch>`，避免不同目标之间覆盖；APK 内部包名、版本、架构和依赖不变。即使 LuCI 是 noarch，也保留各目标独立生成的包及依赖元数据。

每个 OpenWrt Artifact 附带独立的 `<压缩包名称去掉.tar.gz>.SHA256SUMS`，Release 汇总这些清单并在发布前校验。

Release 同时附带总 `SHA256SUMS`、`INSTALL.md`、`BUILDING.md`、`NATIVE.md` 和 `LICENSE`。总清单用于校验压缩包和独立 APK，解压后的清单用于校验两个 APK 和配套文件。

构建信息记录源码提交、SDK、目标、包架构、实际 feed 提交及包版本。构建保留 SDK 自带的 feed 配置和提交锁定，只初始化需要的 `base`、`packages`、`luci`。每个目标的压缩包使用独立名称保存为 Actions Artifact，保留 14 天，最后统一汇总到同一个 Release。工作流只进行构建和文件完整性检查，不运行项目测试或连接路由器。

## 构建缓存与耗时

每个 SDK 单独缓存完整构建目录，包括工具链、feeds、下载文件和依赖编译结果。缓存键包含 SDK SHA-256（同时固定 SDK 自带的 feed 配置）、runner 镜像版本、构建脚本、两份包配方和工作流摘要；不跨架构或不同配置恢复缓存。只有构建和上传成功后才保存缓存，缓存服务不可用时仍可从官方下载 SDK 构建。

缓存命中后跳过 SDK 下载、解压及 feeds 初始化，复用已编译依赖。本项目的 C 源码和 LuCI 文件每次重新复制，先清理两个包及旧 APK，再重新编译打包。因此只修改 C/UI 文件可以复用依赖，而修改依赖配方、SDK 或构建配置会重新建立缓存。默认并行度为 `nproc`，可通过 `JOBS` 覆盖。通过 LuCI 对核心包的依赖关系，从一个 make 入口完成两个包，减少重复依赖扫描。

首次运行仍需编译依赖，并额外上传缓存；优化主要收益在后续命中缓存的运行。完整 SDK 缓存较大，受仓库缓存配额和自动淘汰影响，不能保证每次命中。建议在默认分支手动运行工作流，再用输入框指定发布 tag，让不同发布版本共享默认分支缓存；不同 tag 的工作流缓存并不直接共享，见 [GitHub 缓存范围说明](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache)。需要强制冷构建时，在 Actions → Caches 删除对应 `esurfing-sdk-v1-…` 缓存。

## APK 与 Release 的区别

构建成功不等于 Release 一定发布：每个架构先独立上传 Artifact，Release 要等九个 OpenWrt 和四个原生任务全部成功。如果 macOS 等任意任务失败，Release 会跳过，但成功的 OpenWrt APK 仍在该次运行的 **Artifacts** 中，保留 14 天。最后的 **Build and release summary** 会汇总状态和下载入口；早期工作流的 Artifact 只有 `.tar.gz`，APK 位于压缩包内。

GitHub Run time 表格底部是各 job 的累计时长；矩阵并行执行时，不代表用户等待了同样长的时间。实际耗时还受排队、SDK 下载、缓存恢复和 runner 性能影响。

## macOS / Linux 可执行文件矩阵

`native` job 与 OpenWrt 矩阵并行运行，使用同一个 `source` 提交。四个目标均原生编译，ARM 指 ARM64，x86 指 x86_64：

| 平台 | 架构 | GitHub runner | Release 附件 |
| --- | --- | --- | --- |
| macOS | ARM64 | `macos-15` | `esurfing-macos-arm64.tar.gz` |
| macOS | x86_64 | `macos-15-intel` | `esurfing-macos-x86_64.tar.gz` |
| Linux | ARM64 | `ubuntu-22.04-arm` | `esurfing-linux-arm64.tar.gz` |
| Linux | x86_64 | `ubuntu-22.04` | `esurfing-linux-x86_64.tar.gz` |

使用 [GitHub 官方 runner 标签](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)，脚本会在构建前核对实际 OS/CPU。macOS 最低部署版本设为 15.0，使用 Homebrew 的 curl/xz/expat 动态库；Linux 使用 Ubuntu 22.04 的 glibc 和系统开发库，不使用 OpenWrt 的 musl SDK。

`scripts/build-native.sh` 调用 C Makefile，不执行测试或生成的客户端。macOS 分支通过 `xcrun --sdk macosx` 获取编译器与 SDK，显式设置 `SDKROOT` 和 `-isysroot`，确保 Xcode clang 能找到 `signal.h` 等系统头文件及系统库。压缩包内包含可执行文件 `esurfing`、运行说明 `NATIVE.md`、`BUILD-INFO.txt`、`SHA256SUMS` 和许可证。构建信息记录依赖版本、macOS SDK 路径/版本、Mach-O/ELF 信息和动态库依赖；运行条件见 [原生可执行文件说明](native.md)。

四个目标的 Artifact 使用独立名称保留 14 天。Release job 等待两个矩阵全部成功，将十三份压缩包与总校验和发布到同一 Release；文件名前缀区分 OpenWrt 和普通 macOS/Linux，避免同架构产物混淆。

在相应平台安装工作流列出的开发依赖后，也可生成本机发布目录（输出目录须为空）：

```sh
# Apple Silicon macOS
bash scripts/build-native.sh macos arm64 /path/to/output
# Intel macOS：将 arm64 改为 x86_64
# Linux x86_64
bash scripts/build-native.sh linux x86_64 /path/to/output
# Linux ARM64：将 x86_64 改为 arm64
```

这是原生构建脚本，需要在匹配的系统/CPU 上运行。本地构建的系统及依赖版本以实际环境为准。

## 未签名 APK

工作流和本地脚本生成未签名 APK，不需要签名 Secret，不生成或发布公钥。用户校验 SHA-256 后，通过 `apk add --allow-untrusted` 安装核心包和可选 LuCI 包，详见 [安装说明](install.md)。`--allow-untrusted` 仅用于本次安装，不更改系统软件源的信任配置。

## 本地 SDK 构建

在 Linux 上准备匹配设备的 OpenWrt APK SDK，以及工作流列出的编译依赖。脚本要求 Bash 4.4+、GNU make/sed/coreutils 和 Git；不适用于直接在 macOS 上运行 Linux SDK。

在仓库根目录执行，输出目录须为空：

```sh
bash scripts/build-openwrt.sh /path/to/openwrt-sdk /path/to/output
```

脚本只更新 `base`、`packages`、`luci` feeds，并递归安装所需依赖配方，复制本仓库两份包，关闭全量构建和包签名选项，仅编译目标包及依赖。默认使用全部可用 CPU 核数；内存较少时可在命令前添加 `JOBS=2`。依赖由 SDK 交叉编译，目标包使用 SDK 的 APK 打包流程生成。每次构建先清理这两个包的旧构建结果，避免复用 SDK 时导出此前已签名的产物；输出保留 SHA-256 校验和及构建信息。

新增目标时，在矩阵 `include` 增加 `arch`、`target`、`subtarget`、`sdk`、`sha256`，并更新架构列表。变更 OpenWrt 版本时，同时更新 `OPENWRT_VERSION`、`SDK_BASE_URL` 的版本、各矩阵条目的 SDK 文件名/摘要及文档。每项须使用对应下载页的真实摘要；脚本会核对 SDK 实际 target/subtarget 与包架构，发现不匹配即停止。仅改文件名不能把其他架构的包变成可安装的 APK。切换 SDK 或发布版本建议使用全新解压目录。

包内版本分别取自 `cclient/openwrt/Makefile` 和 `luci-app-esurfing-c/Makefile` 中的 `PKG_VERSION` / `PKG_RELEASE`；Git tag 不会自动改写包版本。升级代码时按需递增包版本或 release 号，再提交和打 tag。当前保留核心 `0.2.0-r2` 与 LuCI `0.2.0-r3`。

## 本机 C 编译

安装 C 编译器、pkg-config 以及 libcurl/liblzma/Expat 开发包后执行：

```sh
make -C cclient
```

生成 `cclient/esurfing`，适用于匹配系统、架构和运行库的机器；不能将 macOS 或 glibc Linux 产物复制到 OpenWrt。本仓库没有迁入原项目的测试目录或 Python 参考实现。

参考：[GitHub 手动工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)、[OpenWrt SDK 文档](https://openwrt.org/docs/guide-developer/toolchain/using_the_sdk)、[OpenWrt 25.12 APK 打包实现](https://github.com/openwrt/openwrt/blob/v25.12.0/include/package-pack.mk)。

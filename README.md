# Esurfing_OpenWrt

江苏天翼校园 HTTP Portal 精简 C 客户端，提供 OpenWrt APK、macOS/Linux 可执行文件，以及可选的 LuCI 中文配置界面和协议分析。核心流程为门户发现、算法协商、获取 ticket、登录、定时保活、自动重连和注销。

运行依赖为 libcurl、liblzma、libexpat；路由器不需要 Python 或 Node.js。支持已识别的双层 3DES-ECB、双层 3DES-CBC 和三层变体 TEA 模板，只解析算法包，不执行服务器下发的 DLL。

## 目录

| 路径 | 内容 |
| --- | --- |
| `cclient/src/` | C 客户端、协议编解码、密码算法和网卡信息读取 |
| `cclient/openwrt/` | OpenWrt 包配方、UCI 配置、procd 服务及凭据示例 |
| `luci-app-esurfing-c/` | 配套 LuCI 前端、ucode RPC 后端、ACL 和包配方 |
| `scripts/build-openwrt.sh` | 使用 Linux OpenWrt SDK 构建核心 APK，可选构建 LuCI APK |
| `scripts/build-native.sh` | 原生构建 macOS/Linux ARM64 和 x86_64 可执行文件 |
| `.github/workflows/release-apk.yml` | 手动构建 OpenWrt APK 和原生程序，发布到同一 Release |
| `docs/protocol.md` | 协议证据、算法包结构、三种算法模板及 C 源码对应关系 |
| `docs/install.md` | APK 安装、校验、LuCI 和服务配置 |
| `docs/native.md` | macOS/Linux 可执行文件的运行库、校验与使用说明 |
| `docs/building.md` | 本地构建及手动发布说明 |

仓库从原研究项目提取 C 实现及配套组件，未迁入 Python 客户端、测试目录、原始抓包、Windows 安装文件、下载的算法映像或旧二进制产物。原研究资料保留在原项目中。

## 安装与使用

从本仓库 [Releases](https://github.com/TNTinMinecraft/Esurfing_OpenWrt/releases) 下载手动工作流生成的文件，按 [安装说明](docs/install.md) 校验并安装。当前工作流使用 **OpenWrt 25.12.0 / musl**，一次构建 x86_64、ARMv7、两种 ARM64、大小端 MIPS/MIPS64 和 RISC-V 64，共九个 SDK 目标。发布九个架构对应的核心 APK 和一份共享的 `noarch` LuCI APK，附带校验文件，不再发布 OpenWrt `.tar.gz`；具体包架构和 SDK 对应关系见 [构建矩阵](docs/building.md)。请选择与设备匹配的核心 APK 和可选的共享 LuCI APK，校验后通过 `apk add --allow-untrusted` 安装。

macOS/Linux 用户可下载 `esurfing-macos-arm64.tar.gz`、`esurfing-macos-x86_64.tar.gz`、`esurfing-linux-arm64.tar.gz` 或 `esurfing-linux-x86_64.tar.gz`，安装匹配的运行库后直接执行压缩包中的 `esurfing`，见 [原生程序说明](docs/native.md)。

`esurfing-c` 是核心包，`luci-app-esurfing-c` 是可选界面。安装后服务默认禁用；使用 **服务 → 天翼校园** 配置账号和逻辑接口，或按 [C 客户端说明](cclient/README.md) 配置凭据、UCI 和服务。

前台运行示例：

```sh
esurfing --interface eth1
```

将 `eth1` 替换为校园网实际接入设备。程序交互询问账号密码；`Ctrl+C` / `SIGTERM` 尝试注销。程序使用当前网络配置，不修改路由、NAT 或防火墙。

## 构建与发布

GitHub Actions **仅支持手动触发**，普通 push、tag push、Pull Request 均不会自动运行。将源码和 tag 手动推送后，在 Actions 中运行 **Build and release APKs and binaries**，输入已有 tag；九个 OpenWrt 目标和四个原生程序目标全部构建成功后汇总生成同一个 Release，或勾选 `draft` 保存为草稿。构建使用 SDK 依赖缓存和按 CPU 核数并行。即使其他平台失败导致 Release 跳过，成功架构的包仍可在该次运行的 Artifacts 下载。完整步骤见 [构建与发布](docs/building.md)。

工作流不包含项目测试步骤。本次迁移未执行编译、测试、推送、Action 或 Release 发布；实际构建结果以之后手动运行的工作流为准。

## 实现边界

未知算法模板、非零 `level` 和额外检测命令会导致退出或服务暂停；不伪造检测结果。`local-time` 使用单调时钟近似原客户端的计数器；实际线路兼容性及长期在线状态仍需在设备上确认。历史协议验证范围与尚未确定的行为见 [协议分析](docs/protocol.md)。

保留目标仓库原有的 [GPLv3 许可证](LICENSE)。

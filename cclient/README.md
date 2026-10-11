# 江苏天翼校园精简 C 客户端

用于低性能 OpenWrt：仅保留门户发现、算法协商、登录、定时保活和注销。单进程、单线程应用逻辑，保活期间阻塞等待，不轮询；不依赖 Python，不执行服务器下发的 DLL。协议分析见 [docs/protocol.md](../docs/protocol.md)。

运行依赖为 **libcurl（7.62+）、liblzma、libexpat** 及它们在固件中的依赖。MD5、SHA-256、DES 和变体 TEA 内置，无需额外链接密码学库；HTTPS 使用 libcurl 的 TLS 后端和系统 CA，保持证书校验。依赖包定义：[libcurl](https://github.com/openwrt/packages/blob/master/net/curl/Makefile)、[liblzma](https://github.com/openwrt/packages/blob/master/utils/xz/Makefile)、[libexpat](https://github.com/openwrt/packages/blob/master/libs/expat/Makefile)。

**0.2.0 新增自动重连、UCI 配置和 init.d/procd 服务。** 工作流使用 OpenWrt 25.12.0 SDK / musl，通过矩阵构建九个目标，覆盖 x86_64、ARMv7、ARM64、MIPS、MIPS64 和 RISC-V 64；完整架构列表见 [构建说明](../docs/building.md)，校园网长期在线仍需验证。开发机 `make` 的 macOS 文件不能复制到路由器，安装包由手动工作流发布到 GitHub Release。

可选安装 [LuCI 中文界面](../luci-app-esurfing-c/README.md)，在浏览器中配置账号、重试参数和服务；[安装说明](../docs/install.md)。界面单独打包，不增加 C 客户端的常驻功能。

## 安装 APK 和启用服务

从 GitHub Release 下载与设备匹配的目标压缩包，将解压后的 APK 复制到路由器。发布包未签名，按随包的安装说明校验 SHA-256，再用 `apk add --allow-untrusted` 安装。依赖应由你固件自己的软件源提供；不要强制忽略架构或依赖错误。停止旧 Python/其他天翼客户端后再启动本服务。

包包含 `/usr/bin/esurfing`、`/etc/init.d/esurfing`、`/etc/config/esurfing` 和凭据示例。**默认 `enabled=0`，不会用占位账号尝试登录。**

```sh
umask 077
vi /etc/esurfing/credentials
# 两行：第一行账号，第二行密码，不加引号
chmod 600 /etc/esurfing/credentials
uci set esurfing.main.enabled='1'
uci set esurfing.main.interface='wan'
uci commit esurfing
/etc/init.d/esurfing enable
/etc/init.d/esurfing start
logread -e esurfing
```

服务配置的 `interface` 是 netifd 逻辑接口（通常为 `wan`），会自动查找它的实际 `l3_device`。如果需要固定物理设备，可设 `uci set esurfing.main.device='eth0.2'` 后 commit/restart；普通命令行 `--interface` 仍填写实际设备名。

```sh
/etc/init.d/esurfing stop       # 尝试注销，停止后不继续重连
/etc/init.d/esurfing restart    # 修改凭据/配置后重启
/etc/init.d/esurfing disable    # 取消开机自启
```

日志通过 procd 进入系统日志缓冲，不另写日志文件。procd 为意外退出配置 30 秒延迟和每小时连续快速退出上限（超过 3 次停止拉起）；正常断线由 C 程序自行处理。停止服务最多给 120 秒用于正在进行的请求和注销。启用或停用自启不会替代 start/stop。

## 自动重试策略

- 开机接口不存在、未获 IPv4/网关、DNS/连接/收发超时、源 IP 暂时不可绑定时，等待后重试。
- HTTP 408、429、500、502、503、504 视为暂时故障。支持 `Retry-After` 的秒数形式（最多 86400 秒）；等待可超过配置的普通退避上限。
- 短暂故障先重试保活，次数遵循服务器 `keep-retry`（默认 5，范围 1–20）。耗尽后重新读取 IP/MAC/网关、发现门户、下载算法、取新 ticket 并登录。
- 保活返回业务错误或重定向/401/403 时，按会话失效重新登录；IP/MAC/网关变化也会重建会话。服务收到 WAN 事件会用 SIGHUP 唤醒等待并刷新会话。
- 重连默认约 5、10、20、40、80、160、300 秒，增加最多 25% 抖动但不超过上限；稳定在线至少 120 秒且保活成功至少两次后重置退避。反复刚登录就掉线不会重置。
- **明确的账号拒绝、凭据文件错误、未知算法、非法 XML、额外检测命令或非零 level 不自动再次提交密码。** 前台模式退出；服务模式记录 `suspended` 并休眠，修复后执行 `/etc/init.d/esurfing restart`。此时 procd 的 `status` 仍可能显示进程运行，要结合日志判断认证状态；普通网络 HUP 不解除暂停。

调节退避：

```sh
uci set esurfing.main.retry_delay='5'
uci set esurfing.main.retry_max='300'
uci commit esurfing
/etc/init.d/esurfing restart
```

前台也默认自动重连；可使用 `--retry-delay 5 --retry-max 300` 调节，或 `--no-retry` 禁止创建新的重连会话（当前会话内仍遵循保活重试次数）。所有休眠均可被退出信号立即唤醒。程序不探测外网、不修改路由/NAT/防火墙。

## OpenWrt 编译

在 Linux 上使用与设备匹配、支持 APK 的 SDK。从仓库根目录执行：

```sh
JOBS=2 bash scripts/build-openwrt.sh /path/to/openwrt-sdk /path/to/empty-output
```

脚本默认同时构建 C 客户端和可选 LuCI 包，设置 `BUILD_LUCI=false` 可仅构建核心包；生成未签名 APK、校验和及构建信息；不运行项目测试。GitHub 手动工作流调用同一脚本，仅 x86 任务额外生成一份共享的 noarch LuCI APK。构建和发布步骤见 [构建与发布](../docs/building.md)。

将核心包手动加入 SDK 时，包目录须包含 `cclient/openwrt/Makefile`、`cclient/src/` 和 `cclient/openwrt/files/`，分别放入 `package/esurfing-c/Makefile`、`src/` 和 `files/`。旧版 SDK 的 `.ipk` 不能重命名为 `.apk`。

## 路由器运行

先确认 WAN 已得到 IPv4、网关和 DNS，并退出原客户端。接口填写 `ubus call network.interface.wan status` 输出里的 `l3_device`，例如 `eth1` 或 `eth0.2`：

```sh
esurfing --interface eth1
```

程序先发现门户并校验算法包，再从终端询问账号密码；密码不回显。Linux 连接绑定所选接口和源 IPv4，需要 root 或相应网络权限。系统 DNS 查询使用系统解析器配置。程序不更改路由、NAT 或防火墙。

登录后立即首次保活，之后按服务器返回的间隔休眠；网络故障和会话失效按上述策略自动恢复。`Ctrl+C` 或 `SIGTERM` 会唤醒等待并尝试注销；HTTP 请求设置 15 秒超时，同步 DNS 解析仍可能受系统解析器超时影响。注销同样可能遇到网络失败，最多处理三次算法更新，procd 停止服务有 120 秒总等待上限。

先确认路由器和下游设备能正常上网，再观察至少两到三轮保活；服务器返回成功不等于已验证互联网连通性。

可选参数：

```sh
esurfing --interface eth1 --gateway 192.0.2.1
esurfing --interface eth1 --portal-url 'http://当前入口/完整URL'
esurfing --interface eth1 --ticket-url 'http://当前服务器/ticket.cgi?完整参数' \
  --auth-url 'http://当前服务器/auth.cgi?完整参数'
```

显式 URL 必须使用当前线路分配的参数，不能复制旧抓包中的 ticket、IP 或 MAC。默认入口仍为 `http://preportal.go189.cn/detect.html`，支持 HTTP 重定向、Refresh 头和常见 meta refresh。

### 非交互凭据

为省去 JSON 解析，C 版使用 **UTF-8 两行文本文件**，与 Python 版 JSON 不兼容：第一行账号，第二行密码，每行最多 511 字节，无空行或额外配置项。文件须归运行用户所有，权限 `600`（只读 `400` 也可），不接受符号链接。

```sh
umask 077
vi /root/esurfing-credentials.conf
chmod 600 /root/esurfing-credentials.conf
esurfing --interface eth1 --credentials /root/esurfing-credentials.conf
```

文件内容示意：

```text
你的账号
你的密码
```

密码中的 `&`、`<`、`>` 自动作 XML 转义，不加引号，不做 JSON 转义。程序不会创建凭据文件或复制密码到其他文件，日志不包含密码、ticket 或响应正文；文件凭据每次重连重新读取、登录后从应用缓冲区清除。交互输入的凭据为自动重连保留在进程内存中，退出或服务暂停时清除。

`client-id` 根据网卡 MAC 稳定生成，无状态文件、无周期性闪存写入。它与旧 Python 客户端的随机 UUID 不同；更换网卡/MAC 后会变化。

## 本机编译

安装 C 编译器、pkg-config，以及 libcurl/liblzma/Expat 开发头文件和库，然后在 `cclient` 目录执行：

```sh
make
./esurfing --help
```

GitHub Release 同时提供 macOS/Linux 的 ARM64 与 x86_64 可执行文件，运行库及系统要求见 [原生程序说明](../docs/native.md)。macOS 会自动读取网卡 IPv4/MAC，网关须显式传入：

```sh
./esurfing --interface en0 --gateway 当前校园网网关
```

本仓库未迁入原项目的 Python 参考实现和测试目录。本次迁移未执行编译或测试，历史协议证据见 [协议分析](../docs/protocol.md)。

## 精简范围与资源

支持双层 3DES-ECB、双层 3DES-CBC 和三层变体 TEA，服务器下发已知模板的新密钥可自动更新。算法映像按 `.text` / `.rdata` SHA-256 和 DES 表严格检查，不认识的模板拒绝处理。

核心程序没有抓包分析/导出命令、内建 GUI、JSON 配置、IPv6 认证或 Windows 额外检测。LuCI 页面作为可选包提供。后台运行由 procd 管理，应用自身不 fork。遇到非零 `level`、`excheck` 或 `zexcheck` 会报错退出/暂停。协议中的 `local-time` 仍使用单调时钟近似，真实线路兼容性与旧版一样有待验证。

程序正文和算法包都设置大小上限。LZMA 字典限制在已声明的输出长度以内，样本的 16 MiB 字典因此缩为约 64 KiB；解压完成立即释放映像和解码器。libcurl/TLS/解析器仍有自己的内存开销，实际路由器 RSS、CPU 和依赖安装体积需要真机测量。

### 0.2.0-r2 算法兼容性修复

新增 2026-10-10 真机取得的双层 3DES-CBC 模板。每一层分别遍历整段数据，使用同一个 8 字节初始向量重新开始 CBC 链；不能把六次 DES 合并为一层 CBC。密钥长度仍为 48 字节，IV 紧随密钥，元数据范围检查同时包含 IV。严格核对新模板的代码、只读常量及 DES 表；不会执行下载的 DLL。

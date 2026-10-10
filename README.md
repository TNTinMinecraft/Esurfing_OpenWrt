# 江苏天翼校园认证：Python 实验客户端

本次线路使用 HTTP Portal：发现入口 → 下载算法包 → 取 ticket → 账号密码认证 → 定时保活 → 注销。抓包中的认证不是 PPPoE 或 802.1X。完整分析见 [docs/protocol.md](docs/protocol.md)。

## 已验证的部分

- 原始抓包的 6 条加密 XML 消息，解密后重新加密与原始正文逐字节相同。
- 3 条请求 XML 可从字段重新构造，与原始明文逐字节相同；保活回归使用抓包里的两个端口，仅用于离线比较。
- 4 个请求的 `CDC-Checksum` 均为 HTTP 正文字节的 MD5。
- 算法包可以用 Python 解包、读取密钥；无需执行 Windows DLL。
- 42 项测试覆盖 DES、三层变体 TEA、算法包、跨算法切换、协议状态，以及 macOS 网卡读取、按网卡查询网关、连接绑定、Linux 网关选择、缺少 unicodedata 的环境、无结束标记 LZMA 和算法包导出回归。

## 在 macOS 上试运行

支持 Intel Mac 和 Apple Silicon，Python 3.8+，无需第三方 Python 包。使用 macOS 自带的 `/sbin/ifconfig`、`/sbin/route` 读取当前网卡信息，不依赖 Linux 的 `/proc` 或 `/sys`。

在终端进入本项目目录，先检查网卡：

```sh
/sbin/ifconfig -l
python3 esurfing.pyz network-info --interface en0
```

`network-info` 只输出当前平台、接口、IPv4、MAC 和网关，不访问认证服务器、不询问凭据、不创建客户端状态文件。`en0` 是示例；USB 有线网卡可能是 `en5`、`en7` 等，应选实际接入校园网的网卡。未获得 IPv4 时，需要先连接对应网络。

接入校园网并在原客户端注销后，运行：

```sh
python3 esurfing.pyz login --interface en0
```

随后交互输入宽带账号密码。流程、服务器保活间隔和 `Ctrl+C` 注销行为与 OpenWrt 版本相同。

macOS 网关通过 `route -n get -inet -ifscope 网卡 default` 按所选接口查询，认证 HTTP/HTTPS 的 IPv4 TCP 连接同时绑定该接口与其源地址。系统 DNS 解析仍使用系统配置；VPN、代理软件的系统级过滤也可能影响连接。程序直接建立连接，不使用环境变量中的 HTTP 代理。

也可手工补充 `--gateway 实际网关`、`--ip 实际IPv4`、`--mac 实际MAC`。其中 `--mac` 只覆盖协议报告值，不修改网卡的硬件/私有 MAC；通常应保持自动读取，使实际接入地址和认证信息一致。

若程序提示 `MAC is a placeholder/unusable address`，表示当前执行环境返回了占位地址（例如 `02:00:00:00:00:00`）。在 macOS 终端执行 `/sbin/ifconfig en0`，查看 `ether` 一行；确认是当前接入网卡的实际地址后，补充 `--mac`：

```sh
python3 esurfing.pyz login --interface en0 --mac '当前网卡的实际MAC'
```

不要填写占位值，也不要直接复制旧 Windows 网卡地址。`network-info` 同样接受 `--mac` 供核对。

macOS 同样使用单调时钟近似原客户端的 `local-time`，并保留抓包中的 `ostag` 和 User-Agent 作为协议兼容参数。这不是已验证的官方 macOS 协议实现；服务端能否接受仍须在线测试。

## 在 OpenWrt 上试运行（Python）

1. 出口需要先获得校园网 IP、网关和 DNS。程序只做上网认证，使用当前网络配置。`--interface` 填 Linux 实际设备名，可从以下输出的 `l3_device` 查到；例如 `eth1`、`eth0.2`。设备名也可能恰好是 `wan`，以实际输出为准。

   ```sh
   ubus call network.interface.wan status
   ```

   `l3_device` 的含义见 [OpenWrt ubus 文档](https://openwrt.org/docs/techref/ubus)。

2. 准备 Python 3.8 或更新版本，检查标准库模块：

   ```sh
   python3 -c 'import lzma, ssl, socket, fcntl, hashlib, dataclasses, xml.etree.ElementTree; print("OK")'
   ```

   OpenWrt 会拆分 Python 标准库；完整 `python3` 元包及 `python3-lzma` 可按当前固件的软件源安装。新版对当前 ASCII 门户域名不要求 IDNA/unicodedata；可用 `python3 esurfing.pyz check-runtime` 检查运行依赖。无需 pip、PyCryptodome、Wine 或 x86 模拟器。参考 [OpenWrt Python 包定义](https://github.com/openwrt/packages/blob/master/lang/python/python3/Makefile) 和 [python3-lzma 包](https://openwrt.org/packages/pkgdata/python3-lzma)。路由器尚不能上网时，需要提前准备这些依赖。

3. 将本目录生成的 **`esurfing.pyz`** 复制到路由器，例如 `/root/esurfing.pyz`。

4. 先在原 Windows 客户端正常注销，再从路由器前台运行。以下 `eth1` 是示例，替换为实际出口设备：

   ```sh
   python3 /root/esurfing.pyz login --interface eth1
   ```

   程序会从设备读取 IPv4、MAC 和默认网关，并交互询问账号密码。HTTP 连接绑定该 IPv4 源地址。账号密码不写入磁盘；客户端 UUID 存于 `~/.config/esurfing/client-id` 以保持重启前后一致。

   正常顺序为“发现入口”“auth 返回成功”“保活成功”。首次保活立即进行，之后遵循服务器返回的间隔；样本为 300 秒。`Ctrl+C` 或 `SIGTERM` 会尝试注销。

5. 在另一终端确认路由器和下游设备实际能访问互联网，并观察至少两到三次保活。HTTP 认证成功与实际可上网分别验证。程序不修改 LAN/NAT/防火墙设置。

若网关读取不到，可补充 `--gateway 实际网关`。若门户发现失败，使用当前线路浏览器跳转后的完整 URL：

```sh
python3 /root/esurfing.pyz login --interface eth1 --portal-url 'http://当前门户/index.cgi?当前参数'
```

也支持同时提供 `--ticket-url '完整URL' --auth-url '完整URL'`。必须保留当前入口分配的完整 query 参数；不要直接复用抓包中 Windows 的 IP、MAC、tag 或 ticket。

可选的 `--credentials /root/credentials.json` 接受 `{"username":"你的账号","password":"你的密码"}`，文件权限必须为 `600`。默认交互方式适合首轮测试，密码不会出现在命令行参数中。

## 当前边界

- **算法兼容性**：支持 AutoZSM 类型 2，以及目前识别的双层 3DES、三层变体 TEA 两种模板。服务器下发相同模板的新密钥/ID 时，可重新读取密钥；未识别的代码段或表结构仍会明确报错。不会执行服务器下发的本机代码。
- **macOS / ARM 的 local-time**：Windows DLL 实际使用 `RDTSC >> 34`，不是日期时间。macOS/Linux 版本用单调时钟纳秒值代替计数源，格式相同，但服务器是否接受仍需实测。`--local-time` 仅用于指定九字符调试值，不建议固定使用抓包旧值。
- **额外检测**：保活中省略 `excheck-args`。原生构造函数允许两个端口为 0/空时省略该项，但这不证明服务器允许长期省略。尚未实现 UDP/TCP 检测服务、Windows 进程/驱动检查，以及 `excheck`/`zexcheck` 命令。遇到额外命令或非零 `level` 会报错并结束会话。
- **稳定性**：实现了保活网络错误的有限重试，次数采用服务器 `keep-retry`；未实现断网后重新获取 IP、自动重新发现门户和登录。认证错误不会反复提交账号密码。首轮验证后再考虑常驻服务。
- **注销**：XML 字段来自 `PortalServer.dll` 静态分析和原客户端成功注销日志；现有抓包不包含 `term.cgi`，所以注销尚无报文级或线路验证。

不要把当前版本当作已经验证可长期运行的 OpenWrt 上网方案。若出现 `Server Error-Code`、未知算法或检测命令，保留程序的简短错误输出和新抓包，可以继续定位协议差异。

## 算法包解压失败的诊断

旧版用 `max_length=包头长度+1` 解压 AutoZSM。此格式的 LZMA 流可能没有结束标记，额外尝试解压一字节会把编码器尾部数据继续当作压缩符号，导致有效包被误报为 `Algorithm decompressed size mismatch`。现已按原客户端的输出长度上限停止，并保留不足长度的错误检查及后续 PE/算法模板校验。

新版登录先协商并检查算法包，成功后才询问账号密码。若换包后仍在算法阶段报错，可直接下载初始算法包：

```sh
python3 /root/esurfing.pyz fetch-algorithm --interface wan --output /tmp/esurfing-algorithm.bin
```

该命令发现门户后仅发送最初的固定协商正文，不询问、不发送账号密码，不申请 ticket，不登录。原始算法响应以 `600` 权限保存，然后才尝试解析；即使报告不支持或解压失败，文件仍可取回分析。文件已存在时拒绝覆盖，重试请换一个输出文件名。

把该文件复制回分析电脑后，可离线检查：

```sh
python3 esurfing.pyz inspect-algorithm /路径/esurfing-algorithm.bin
```

请保留算法包和终端中包含预期/实际长度的错误输出。无需发送账号密码或完整认证抓包。

## 本地复核与构建

在本项目目录执行：

```sh
python3 -m unittest discover -s tests -v
python3 -m esurfing verify-pcap dump1.pcapng
python3 tools/build_zipapp.py
python3 esurfing.pyz --help
```

离线验证需要 tshark；会自动寻找 macOS Wireshark 自带版本，也可指定 `--tshark /路径/tshark`。该命令不发送网络请求，也不输出解密后的账号、密码或 ticket。复制给他人时仅分享代码/说明；原抓包和 `analysis/private/` 中的解密文件含敏感信息。
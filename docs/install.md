# APK 安装与服务配置

## 下载与校验

从 GitHub Release 下载与设备匹配的 `esurfing-c-<版本>-<target>-<subtarget>-<arch>.apk`、总 `SHA256SUMS`，以及可选的 `luci-app-esurfing-c-<版本>-noarch.apk`。OpenWrt 不再额外发布 `.tar.gz`；APK 可以直接安装。

工作流覆盖 x86_64、ARMv7、两种 ARM64、大小端 MIPS/MIPS64 和 RISC-V 64，均基于 OpenWrt 25.12.0 / musl。具体 SDK/包架构对应表见 Release 附件 `BUILDING.md`。用 `ubus call system board` 查看设备 target，并用 `apk --print-arch` 查看包架构；应同时核对固件版本、具体架构及软件源。`mips64_mips64r2` 并不是所有 MIPS64 设备的通用包。

以下以 `ramips/mt7621` 的 `mipsel_24kc` 为例，在仅存放本次所需 APK 的目录执行，版本号和文件名以 Release 为准：

```sh
core='esurfing-c-0.2.0-r2-ramips-mt7621-mipsel_24kc.apk'
luci='luci-app-esurfing-c-0.2.0-r3-noarch.apk'
# 只校验已下载的文件；未安装 LuCI 时省略第二组命令
awk -v file="$core" '$2 == file { print; found=1 } END { if (!found) exit 1 }' SHA256SUMS > core.sha256 && sha256sum -c core.sha256
awk -v file="$luci" '$2 == file { print; found=1 } END { if (!found) exit 1 }' SHA256SUMS > luci.sha256 && sha256sum -c luci.sha256
```

LuCI APK 内部架构为 `noarch`，只构建一份，供本次发布的各 OpenWrt CPU 架构共用。运行仍需要兼容的现代 LuCI、rpcd 和 ucode，以及适合设备架构的核心客户端。APK 不能安装在使用 opkg/IPK 的旧固件中。

若没有 Release，请进入对应 Actions 运行的 **Artifacts** 下载 `openwrt-apk-<target>-<subtarget>-<arch>`；共享 LuCI APK 从 `openwrt-apk-x86-64-x86_64` 获取，只取其中的 LuCI APK，不要安装不匹配的 x86 核心包。各 Artifact 附带该目标的 `.SHA256SUMS` 和 `.BUILD-INFO.txt`；完整解压后可用 `sha256sum -c <清单文件名>` 校验。其他平台失败可能阻止 Release 发布，但不影响成功目标的 Artifact。早期工作流的 Artifact 内还有 `.tar.gz`，其中包含 APK。

这些 APK 未签名。完成 SHA-256 校验后，使用 `--allow-untrusted` 忽略签名要求并安装：

```sh
apk update
apk add --allow-untrusted ./esurfing-c-*.apk
# 可选：需要系统已安装 LuCI
apk add --allow-untrusted ./luci-app-esurfing-c-*.apk
```

依赖由当前固件自己的软件源提供，发布附件不捆绑依赖库；网络尚未可用时须先准备匹配的离线依赖。`--allow-untrusted` 只忽略签名要求，仍需满足架构与依赖条件。安装或升级本项目的本地 APK 时使用相同参数，无需导入公钥。

## LuCI 配置

刷新浏览器后进入 **服务 → 天翼校园**：

1. 填写账号密码，选择校园网逻辑接口（通常为 `wan`）。
2. 勾选“启用服务”，保存并应用。
3. 点击“启动 / 重新登录”；需要开机启动时再启用自启。

修改已有账号时密码留空会保留原密码。密码保存在 `/etc/esurfing/credentials`，不会写入 UCI 或回传浏览器。配置“启用服务”和 init.d 开机自启是两个独立开关。

状态中的进程运行不等于认证成功；服务遇到永久性错误可能保持进程但暂停认证，请结合日志判断。

## 命令行配置

包内默认 `enabled=0`。停止其他天翼客户端后，手动创建两行凭据文件：第一行账号，第二行密码，不加引号。

```sh
umask 077
vi /etc/esurfing/credentials
chmod 600 /etc/esurfing/credentials
uci set esurfing.main.enabled='1'
uci set esurfing.main.interface='wan'
uci commit esurfing
/etc/init.d/esurfing enable
/etc/init.d/esurfing start
logread -e esurfing
```

UCI 的 `interface` 填 netifd 逻辑接口，服务会解析其 `l3_device`；命令行 `esurfing --interface` 则填写实际网络设备。需要覆盖物理设备时设置 `esurfing.main.device`。

```sh
/etc/init.d/esurfing stop       # 停止并尝试注销
/etc/init.d/esurfing restart    # 修改配置后重启，或解除认证暂停
/etc/init.d/esurfing disable    # 取消开机启动
```

若要持续停用，还需把 `esurfing.main.enabled` 设为 `0` 并提交，避免后续网络事件重新启动服务。

保活成功只表示认证服务器接受请求，应另外确认路由器及下游设备能上网。未知模板或额外检测要求当前不支持；勿把历史协议比对等同于长期线路验证。

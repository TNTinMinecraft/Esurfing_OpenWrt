# 天翼校园 LuCI 界面

为 `esurfing-c 0.2.0` 提供精简中文界面，入口为 **服务 → 天翼校园**。兼容本仓库的 UCI 配置与 init.d/procd 服务，前端使用原生 LuCI JavaScript 表单，后端作为 ucode 模块加载到现有 rpcd。没有新增守护进程，不依赖 Python 或 Node.js。

安装包内部架构是 `noarch`；多架构工作流只在 x86 SDK 任务中构建一次，各 OpenWrt CPU 架构共用同一个 `luci-app-esurfing-c-*-noarch.apk`，以 `apk add --allow-untrusted` 安装。运行环境为 OpenWrt 25.12 的现代 LuCI；需要已安装 C 客户端、LuCI、rpcd 的 ucode 插件及 fs/uci/ubus 模块。安装步骤见 [LuCI 安装说明](../docs/install.md)。

## 页面功能

- 账号编辑：首次填写账号密码，后续密码留空保留原值；已存密码不会发送到浏览器。
- 基本设置：启用服务、选择校园网逻辑接口。
- 自动重试：初始与最大等待秒数，检查上下界及大小关系。
- 高级设置：固定设备、网关覆盖、门户入口、配套 Ticket/认证地址。
- 服务操作：启动/重新登录、停止、独立开启或关闭开机启动。
- 状态与日志：每 10 秒读取进程和自启状态；页面隐藏时停止发起状态查询；日志手动读取最近 100 条本客户端日志，最多 32 KiB。

“启用服务”是 C 客户端的配置开关，“开机启动”控制 init.d 自启链接，两者独立。停止服务不会取消开机启动；开启自启也不会立即启动。网络事件可能重新加载已启用的服务，要持续停用请关闭配置中的“启用服务”并保存应用。

表单使用 LuCI 标准“保存 / 保存应用 / 重置”。账号对话框和服务按钮即时执行，不自动保存尚未应用的表单更改，也不受表单“重置”影响。修改账号后点击“启动 / 重新登录”，可解除因错误账号或协议问题导致的服务暂停。

状态只显示 procd 进程是否存在，不将其等同于认证成功或互联网连通。服务暂停后进程仍可能运行，请查看日志。

## 凭据与权限

账号写入原有 `/etc/esurfing/credentials` 两行文件。后端验证长度、禁止换行/NUL，检查目录与文件所有权，使用私有临时目录和原子重命名更新，最终权限为 `0600`。写入失败不覆盖旧账号。账号密码不会放进 shell 命令参数、UCI 或后端错误响应。

如果已有 UCI 配置使用自定义凭据路径，界面继续支持服务控制，但禁止在页面中读写该路径，须在命令行维护账号。ACL 只授予本应用的 RPC 方法及对应 UCI 权限，没有开放任意文件读写或通用命令执行接口。只读 LuCI 会话禁用修改和控制按钮。

## 构建

仓库根目录的统一构建脚本会把 C 客户端和此 LuCI 包一起放入 OpenWrt SDK，生成两个未签名 APK：

```sh
JOBS=2 bash scripts/build-openwrt.sh /path/to/openwrt-sdk /path/to/empty-output
```

脚本使用 SDK 配方生成依赖、安装脚本和文件清单；GitHub 手动工作流调用同一脚本，仅 x86 任务启用 LuCI，其他任务设置 `BUILD_LUCI=false`。详见 [构建与发布](../docs/building.md)。本次未迁入原项目的独立手工打包脚本和测试目录，也未执行测试。

## 版本记录与范围

- 0.2.0-r2 的历史记录包含 ImmortalWrt SNAPSHOT 上的升级、rpcd 注册、状态查询和页面加载；这些不代表校园网认证已得到验证。
- 0.2.0-r3 修正 `logread` 的基础正则过滤，并在服务日志过滤后保留最近 100 行、最多 32 KiB。

实际校园网认证和长期在线仍需在设备上确认。

接口依据：[LuCI 25.12 源码](https://github.com/openwrt/luci/tree/openwrt-25.12)、[rpcd ucode 插件示例](https://github.com/openwrt/rpcd/blob/master/examples/ucode/example-plugin.uc)。

# 微信 4.1.13（269629）禁止更新逆向报告

> 分析日期：2026-09-18
>
> 报告类型：普通 Mach-O 逆向，`flavor = null`
> 工具链：Xcode LLVM、Apple `otool`/`codesign`、Python 3、Swift 6

## 1. 执行摘要

本次针对本机微信 4.1.13（构建号 269629）的 Sparkle 更新入口进行离线分析。
从 `XAppUpdateManager` 的 Objective-C 相对方法列表定位了六个更新控制点，并将其
ARM64 方法入口改为立即返回 `false`。补丁带有原始字节保护，版本或样本不匹配时
会拒绝写入。配置已先在完整应用副本中验证，随后安装到 `/Applications/WeChat.app`；
最终只有配置覆盖的 ARM64 方法入口发生变化，x86_64 代码段未变，应用深度签名验证通过。

## 2. 范围

- 授权与范围：[scope.md](../work/wechat-269629-update/scope.md)
- 网络模式：`offline`
- 分析对象：`Contents/Resources/wechat.dylib`
- 原始 universal dylib SHA-256：`67ad41d6c7b59b497c74e3f93095e5762a4312707a3462baaeaffe5347c4ea8e`
- 不涉及网络服务、账号数据、聊天内容、防撤回或多开。

## 3. 静态分析

目标是 x86_64/ARM64 universal Mach-O，并依赖 Sparkle。`XAppUpdateManager` 保留了
稳定的 Objective-C 选择器。验证脚本解析 `__objc_selrefs` 和相对格式的
`__objc_methlist`，将选择器引用映射到 ARM64 IMP，而不是依赖字符串附近地址猜测。

| 选择器 | ARM64 VA | 原始入口字节 |
| --- | ---: | --- |
| `startUpdater` | `0x27becc` | `FC6FBBA9F85F01A9` |
| `checkForUpdates:` | `0x27e0f8` | `FFC305D1FC6F14A9` |
| `startBackgroundUpdatesCheck:` | `0x27e3c8` | `FF4301D1F44F03A9` |
| `enableAutoUpdate:` | `0x27e7e8` | `F657BDA9F44F01A9` |
| `automaticallyDownloadsUpdates` | `0x289060` | `00604039C0035FD6` |
| `canCheckForUpdate` | `0x289070` | `00644039C0035FD6` |

六处均替换为 `00008052C0035FD6`，对应：

```asm
mov w0, #0
ret
```

## 4. 核心发现

### F-001
- title: 构建 269629 的更新路径可由六个 XAppUpdateManager 入口完整封锁
- severity: n/a_re
- category: reverse_algo
- status: validated
- evidence_ids: [E-002, E-003, E-004]
- location: `Contents/Resources/wechat.dylib` ARM64 `0x27becc` 等六处
- impact: 更新器不会启动，后台/手动检查和自动下载入口返回禁用状态。
- confidence: high
- repro_steps:
  1. 对原始 dylib 运行 `scripts/verify_update_269629.py`。
  2. 用 `wechattweak patch` 修改完整应用副本。
  3. 比较两架构的 `__text` 并运行 `codesign --verify --deep --strict`。
- remediation: n/a

## 5. 调用路径

### P-001
- title: 从 Objective-C 选择器到已验证安装补丁
- path_type: callflow
- start: `XAppUpdateManager` 更新选择器
- goal: 构建 269629 的更新入口固定返回 `false`
- steps:
  1. 解析选择器引用及相对方法列表，得到六个 IMP — evidence: E-002 — finding: F-001
  2. 校验入口原始字节并写入 return-false 指令 — evidence: E-002 — finding: F-001
  3. 在完整副本中比较代码段并验证签名 — evidence: E-003 — finding: F-001
  4. 安装到正式应用并重复字节与签名验证 — evidence: E-004 — finding: F-001
- residual_risks: 微信升级后构建号和实现地址可能变化，必须重新适配，当前配置会因版本或 expected 字节不匹配而拒绝写入。

## 6. 复现

```bash
swift build -c release
python3 scripts/verify_update_269629.py /Applications/WeChat.app/Contents/Resources/wechat.dylib
.build/release/wechattweak patch -a /Applications/WeChat.app -c "$PWD/config.json"
/usr/bin/codesign --verify --all-architectures --deep --strict /Applications/WeChat.app
```

静态验证脚本要求输入未打补丁的原始 dylib；安装后的文件应通过配置字节或与安装前备份比较验证。

## 7. 时间线与附件

- 时间线：[timeline.md](../work/wechat-269629-update/timeline.md)
- 配置：[config.json](../config.json)
- 自动验证脚本：[verify_update_269629.py](../scripts/verify_update_269629.py)
- 适配说明：[wechat-269629.md](wechat-269629.md)

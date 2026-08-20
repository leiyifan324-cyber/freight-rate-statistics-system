# v1.1.0 发布说明

本版本完整保留QQ群运价接收、持久队列、正式/待生效/不合格分类、Excel增量写入、大小类统计、春节年度归档、生命周期和实时管理页面，并将NapCatQQ安装组件纳入同一个Windows发行包。

## 下载选择

- 推荐：`FreightQuoteSystem-Setup-v1.1.0.exe`
- 免安装：`FreightQuoteSystem-Portable-v1.1.0.zip`
- 校验：`SHA256SUMS.txt`

## 一体化内容

- 物流运价统计系统及全部Python运行环境，无需安装Python。
- NapCatQQ官方 `NapCat.Shell.Windows.Node.zip` v4.18.19及Node运行环境。
- NapCatQQ资产SHA-256校验、完整许可、官方来源和稳定启动器生成脚本。
- 不包含腾讯QQ；用户必须自行安装官方64位QQ并扫码登录。

## 重要许可说明

NapCatQQ采用Limited Redistribution License，并限制商业使用。此Release只按其许可进行原样非商业再分发；任何商业部署必须先取得NapCat作者额外授权。

## 已知限制

- 仅支持64位Windows 10/11。
- 安装包没有商业代码签名，Windows可能显示“未知发布者”。
- OCR依赖Windows简体中文语言能力。
- QQ或NapCat升级后，应重新确认OneBot端口、Token和登录状态。

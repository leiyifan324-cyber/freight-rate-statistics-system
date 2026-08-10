# v1.0.0 发布说明

首个正式Windows版本，面向无需Python或命令行经验的物流报价统计用户。

## 下载选择

- 推荐：`FreightQuoteSystem-Setup-v1.0.0.exe`
- 免安装：`FreightQuoteSystem-Portable-v1.0.0.zip`
- 校验：`SHA256SUMS.txt`

## 首次使用

安装后仍需从官方渠道安装NapCatQQ、扫码登录专用QQ，并在本机管理页添加群和线路。完整步骤见仓库 [README](../README.md) 与 [首次部署](02-首次部署.md)。

## 已知限制

- 仅支持64位Windows 10/11。
- OCR依赖Windows简体中文语言能力，复杂图片可能进入未识别队列。
- NapCatQQ升级后可能需要重新确认WebSocket端口、Token和启动器路径。
- 安装程序没有商业代码签名，Windows可能显示未知发布者。

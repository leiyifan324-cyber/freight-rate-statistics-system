# 物流运价统计系统 v1.1.1

这是Windows便携版启动脚本修复版本，统计业务功能与 `v1.1.0` 保持一致。

## 修复

- 所有发行包 `.bat`/`.cmd` 强制使用Windows CRLF换行和ASCII内容。
- NapCat安装批处理改用ASCII PowerShell脚本文件名，兼容默认中文Windows代码页。
- 新增发行包批处理格式校验和真实 `cmd.exe --check` 冒烟测试。

## 使用

1. 解压便携包后双击 `安装NapCatQQ.bat`。
2. 安装并登录官方64位QQ，完成NapCat扫码与WebSocket配置。
3. 双击 `启动系统.bat`，在管理页配置群与线路。

本发行物不包含腾讯QQ。NapCatQQ许可证、来源与原始官方压缩包继续完整保留。

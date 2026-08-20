# 物流运价统计系统 v1.1.2

这是NapCatQQ启动路径修复版本，统计业务功能与 `v1.1.1` 保持一致。

## 修复

- 稳定启动器根据自身位置读取实际NapCatQQ安装目录，不再依赖外部当前工作目录。
- 调用官方启动脚本前切换到版本目录下的 `napcat` 文件夹，确保正确找到 `NapCatWinBootMain.exe`、注入组件和主程序。
- 新增启动器 `--check` 回归验证，发布构建会实际检查安装路径和命令行工作目录。

## 使用

1. 首次安装或更新时运行 `安装NapCatQQ.bat`。
2. 运行 `%LOCALAPPDATA%\NapCatQQ\Start-NapCat.cmd` 并完成QQ登录。
3. 双击 `启动系统.bat`，等待管理页显示 `connected`。

本发行物不包含腾讯QQ。NapCatQQ许可证、来源与原始官方压缩包继续完整保留。

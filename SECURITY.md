# Security Policy

## Supported Versions

仅支持最新GitHub Release。

## Reporting

请通过GitHub Security Advisory私下报告安全问题，不要在公开Issue中发布Token、QQ号、群号、聊天记录、数据库或真实报价。

## Deployment Boundary

状态页、NapCat WebUI和OneBot WebSocket应只监听 `127.0.0.1`。项目没有设计公网认证层，禁止直接暴露到局域网或互联网。

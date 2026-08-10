#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #error SourceDir must point to the prepared release folder
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{F82C2ED2-B729-4E1E-99EF-59C50DC31C13}
AppName=物流运价统计系统
AppVersion={#AppVersion}
AppPublisher=leiyifan324-cyber
AppPublisherURL=https://github.com/leiyifan324-cyber/freight-rate-statistics-system
AppSupportURL=https://github.com/leiyifan324-cyber/freight-rate-statistics-system/issues
DefaultDirName={localappdata}\FreightQuoteSystem
DefaultGroupName=物流运价统计系统
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename=FreightQuoteSystem-Setup-v{#AppVersion}
UninstallDisplayName=物流运价统计系统
AppMutex=Local\FreightRateStatisticsSystemSupervisor

[Languages]
Name: "chinesesimp"; MessagesFile: ".\ChineseSimplified.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "qq_live_config.json,freight_rules.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\qq_live_config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#SourceDir}\freight_rules.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\启动物流运价系统"; Filename: "{app}\FreightQuoteSystem.exe"; Parameters: "--mode supervisor"
Name: "{group}\群与线路管理"; Filename: "http://127.0.0.1:8765/?view=config"
Name: "{group}\手动文本统计"; Filename: "{app}\FreightQuoteSystem.exe"; Parameters: "--mode manual"
Name: "{group}\使用说明"; Filename: "{app}\docs\03-正常使用.md"
Name: "{autodesktop}\物流运价系统管理"; Filename: "http://127.0.0.1:8765/?view=config"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面管理入口"; GroupDescription: "快捷方式："; Flags: checkedonce
Name: "autostart"; Description: "登录 Windows 后自动启动统计系统"; GroupDescription: "自动运行："; Flags: checkedonce

[Run]
Filename: "{app}\FreightQuoteSystem.exe"; Parameters: "--mode install-autostart"; Tasks: autostart; Flags: runhidden waituntilterminated
Filename: "{app}\FreightQuoteSystem.exe"; Parameters: "--mode supervisor"; Flags: nowait runhidden postinstall skipifsilent; Description: "启动物流运价统计系统"
Filename: "http://127.0.0.1:8765/?view=config"; Flags: shellexec nowait postinstall skipifsilent; Description: "打开首次配置页面"

[UninstallRun]
Filename: "{app}\FreightQuoteSystem.exe"; Parameters: "--mode uninstall-autostart"; RunOnceId: "RemoveAutostart"; Flags: runhidden waituntilterminated
Filename: "{app}\FreightQuoteSystem.exe"; Parameters: "--mode shutdown"; RunOnceId: "ShutdownApplication"; Flags: runhidden waituntilterminated

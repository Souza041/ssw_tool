#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif
#ifndef SourceRoot
  #define SourceRoot "."
#endif

[Setup]
AppId={{C80DEBF4-04D8-4D5D-961B-C6007B7F2CB2}
AppName=Nexus Local Agent
AppVersion={#MyAppVersion}
AppPublisher=Rodobras
DefaultDirName={localappdata}\Programs\Nexus Local Agent
DefaultGroupName=Nexus Local Agent
OutputDir={#SourceRoot}\dist\installer
OutputBaseFilename=NexusAgentSetup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\NexusAgent.exe
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#SourceRoot}\dist\NexusAgent.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Nexus Local Agent"; Filename: "{app}\NexusAgent.exe"
Name: "{userstartup}\Nexus Local Agent"; Filename: "{app}\NexusAgent.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\NexusAgent.exe"; Description: "Iniciar Nexus Local Agent"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /IM NexusAgent.exe /F"; Flags: runhidden; RunOnceId: "StopNexusAgent"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Nexus\Agent"

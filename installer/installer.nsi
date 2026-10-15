Unicode true
!define APP "飞书 Bot 桥"
!define EXE "FeishuBotBridge.exe"
Name "${APP}"
Icon "..\assets\icon.ico"
OutFile "..\dist\FeishuBotBridge-Setup.exe"
InstallDir "$LOCALAPPDATA\FeishuBotBridge"
RequestExecutionLevel user
!include "MUI2.nsh"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Section
  InitPluginsDir
  nsExec::Exec 'taskkill /F /IM ${EXE}'
  Sleep 1500
  SetOutPath "$INSTDIR"
  File "..\dist\${EXE}"
  ; WebView2 runtime (usually present on Win10/11); install silently via bundled bootstrapper if missing
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 +2
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 wv2done
    File "/oname=$PLUGINSDIR\wv2.exe" "MicrosoftEdgeWebview2Setup.exe"
    ExecWait '"$PLUGINSDIR\wv2.exe" /silent /install'
  wv2done:
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\${EXE}" 0
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FeishuBotBridge" "DisplayName" "${APP}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FeishuBotBridge" "UninstallString" "$INSTDIR\uninstall.exe"
SectionEnd
Section "Uninstall"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\uninstall.exe"
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "FeishuBotBridge"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FeishuBotBridge"
  RMDir "$INSTDIR"
SectionEnd

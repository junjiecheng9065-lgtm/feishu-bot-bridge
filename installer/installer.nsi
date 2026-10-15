Unicode true
!define APP "飞书 Bot 桥"
!define EXE "FeishuBotBridge.exe"
Name "${APP}"
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
  SetOutPath "$INSTDIR"
  File "..\dist\${EXE}"
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}"
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

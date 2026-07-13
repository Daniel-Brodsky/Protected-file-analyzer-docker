rule Suspicious_Script_Execution_Strings
{
  meta:
    description = "Common script execution and download primitives"
    scope = "triage"
  strings:
    $a1 = "powershell" ascii wide nocase
    $a2 = "Invoke-Expression" ascii wide nocase
    $a3 = "DownloadString" ascii wide nocase
    $a4 = "FromBase64String" ascii wide nocase
    $a5 = "WScript.Shell" ascii wide nocase
    $a6 = "cmd.exe /c" ascii wide nocase
  condition:
    2 of them
}

rule Office_AutoExec_Indicators
{
  meta:
    description = "Office auto-execution keywords"
    scope = "triage"
  strings:
    $m1 = "AutoOpen" ascii wide nocase
    $m2 = "Document_Open" ascii wide nocase
    $m3 = "Workbook_Open" ascii wide nocase
    $m4 = "Shell(" ascii wide nocase
    $m5 = "CreateObject(" ascii wide nocase
  condition:
    any of ($m1,$m2,$m3) and any of ($m4,$m5)
}

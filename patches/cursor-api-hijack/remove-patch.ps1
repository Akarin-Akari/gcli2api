# Cursor API Hijack Patch - Remove Script (Windows PowerShell)
#
# 从 Cursor 的 workbench.desktop.main.js 文件中移除 hijack.js 补丁
#
# @version 1.0.0
# @author 浮浮酱 (Claude Opus 4.5)
# @date 2026-01-20

param (
    [string]$CursorPath = "C:\Program Files\cursor",
    [switch]$UseLatestBackup,
    [string]$BackupFile,
    [switch]$DryRun
)

# 严格模式
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 颜色输出函数
function Write-Success { param([string]$Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Info { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

# 标题
Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  Cursor API Hijack Patch - Uninstaller" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host ""

# 检查管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "需要管理员权限才能修改 Cursor 文件"
    Write-Info "请以管理员身份运行 PowerShell"
    exit 1
}

# 路径定义
$targetFile = Join-Path $CursorPath "resources\app\out\vs\workbench\workbench.desktop.main.js"
$backupDir = Join-Path $CursorPath "resources\app\out\vs\workbench\backups"

# 检查目标文件是否存在
if (-not (Test-Path $targetFile)) {
    Write-Err "找不到 Cursor 核心文件: $targetFile"
    Write-Info "请确认 Cursor 安装路径是否正确: $CursorPath"
    exit 1
}

Write-Info "目标文件: $targetFile"

# 检查是否有补丁
$targetContent = Get-Content -Path $targetFile -Raw -Encoding UTF8
$patchMarker = "__GCLI2API_HIJACK_VERSION"

if (-not ($targetContent -match $patchMarker)) {
    Write-Warn "未检测到补丁，无需卸载"
    exit 0
}

Write-Info "检测到已安装的补丁"

# 确定恢复方式
$restoreFile = $null

if ($BackupFile) {
    # 使用指定的备份文件
    if (Test-Path $BackupFile) {
        $restoreFile = $BackupFile
    } else {
        Write-Err "指定的备份文件不存在: $BackupFile"
        exit 1
    }
} elseif ($UseLatestBackup -or (-not $BackupFile)) {
    # 使用最新的备份文件
    if (Test-Path $backupDir) {
        $backups = Get-ChildItem -Path $backupDir -Filter "*.backup.*" | Sort-Object LastWriteTime -Descending
        if ($backups.Count -gt 0) {
            $restoreFile = $backups[0].FullName
            Write-Info "找到最新备份: $restoreFile"
        }
    }
}

if (-not $restoreFile) {
    Write-Warn "没有找到备份文件，将尝试手动移除补丁代码..."

    # 手动移除补丁代码
    # 补丁格式：以 /** GCLI2API 开头，以 ***/(...)(function()...)(); 结束
    $pattern = "/\*\*\s*\n\s*\*\s*GCLI2API Cursor Hijack Patch[\s\S]*?\*\*\*/\s*\(function\(\)\s*\{[\s\S]*?\}\)\(\);\s*"

    if ($targetContent -match $pattern) {
        $cleanedContent = $targetContent -replace $pattern, ""
        Write-Info "补丁代码已识别并准备移除"

        if (-not $DryRun) {
            $utf8NoBOM = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($targetFile, $cleanedContent, $utf8NoBOM)
            Write-Success "补丁已移除"
        } else {
            Write-Warn "[DryRun] 跳过实际写入操作"
        }
    } else {
        Write-Err "无法识别补丁代码格式，请手动恢复备份"
        Write-Info "备份目录: $backupDir"
        exit 1
    }
} else {
    # 使用备份文件恢复
    Write-Info "使用备份文件恢复: $restoreFile"

    if (-not $DryRun) {
        Copy-Item -Path $restoreFile -Destination $targetFile -Force
        Write-Success "文件已恢复"
    } else {
        Write-Warn "[DryRun] 跳过实际恢复操作"
    }
}

# 验证
if (-not $DryRun) {
    $verifyContent = Get-Content -Path $targetFile -Raw -Encoding UTF8
    if ($verifyContent -match $patchMarker) {
        Write-Err "验证失败：补丁可能未完全移除"
        exit 1
    } else {
        Write-Success "验证通过：补丁已完全移除"
    }
}

# 提示
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "           补丁卸载完成！              " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Info "请重启 Cursor 使更改生效"
Write-Host ""

# 列出可用的备份
if (Test-Path $backupDir) {
    $backups = Get-ChildItem -Path $backupDir -Filter "*.backup.*" | Sort-Object LastWriteTime -Descending
    if ($backups.Count -gt 0) {
        Write-Info "可用的备份文件:"
        foreach ($backup in $backups | Select-Object -First 5) {
            Write-Host "  - $($backup.Name)" -ForegroundColor White
        }
        if ($backups.Count -gt 5) {
            Write-Host "  ... 还有 $($backups.Count - 5) 个备份" -ForegroundColor Gray
        }
    }
}
Write-Host ""

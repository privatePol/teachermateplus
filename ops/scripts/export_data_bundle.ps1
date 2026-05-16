param(
    [ValidateSet("setup", "operational")]
    [string]$Mode = "setup",
    [string]$OutputPath = "",
    [switch]$IncludeNaturalKeys
)

$ErrorActionPreference = "Stop"

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = if ($Mode -eq "setup") {
        "setup_bundle_$timestamp.json"
    } else {
        "operational_bundle_$timestamp.json"
    }
}

$resolvedOutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutputPath
if ($outputDirectory -and -not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}
$temporaryOutputPath = "$resolvedOutputPath.tmp"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$strictUtf8NoBom = [System.Text.UTF8Encoding]::new($false, $true)

$setupModels = @(
    "tenants.Tenant",
    "tenants.Campus",
    "tenants.Department",
    "tenants.Program",
    "tenants.SystemSetting",
    "accounts.User",
    "rbac.Role",
    "rbac.Permission",
    "rbac.RolePermission",
    "rbac.UserRole",
    "rbac.UserPermission",
    "navigation.MenuGroup",
    "navigation.MenuItem",
    "navigation.MenuItemPermission",
    "academics.AcademicYear",
    "academics.Term",
    "academics.TenantTermGradingPeriod",
    "academics.ActiveGradingPeriodSetting",
    "academics.Course",
    "academics.Section",
    "academics.CourseOffering",
    "academics.FacultyAssignment",
    "students.Student",
    "enrollment.Enrollment",
    "grading.GradingTemplate",
    "grading.GradingTemplateApprovalWorkflow",
    "grading.GradingTemplateApprovalStep",
    "grading.GradingTemplatePeriod",
    "grading.GradingTemplateComponent",
    "grading.GradingTemplateSubcomponent",
    "grading.GradingTemplateDetail",
    "grading.CourseTemplateAssignment",
    "grading.CourseBaseValueOverride",
    "grading.TenantGradingProfile",
    "grading.CorrectionApprovalRouteRule",
    "grading.GradingPeriodLock"
)

$operationalExtraModels = @(
    "grading.TemplateHotfixRequest",
    "grading.TemplateHotfixWorkflowStep",
    "grading.GradeActivity",
    "grading.StudentActivityScore",
    "grading.StudentPeriodGrade",
    "grading.StudentFinalGrade",
    "grading.GradeSubmission",
    "grading.GradeSubmissionReopenRequest",
    "grading.GradeCorrectionRequest",
    "grading.GradeCorrectionApprovalStep",
    "grading.GradeCorrectionRequestItem",
    "grading.GradeCorrectionAttachment",
    "grading.GradeCorrectionUnlockWindow",
    "attendance.AttendanceSession",
    "attendance.AttendanceRecord",
    "notifications.FacultyReminder",
    "notifications.FacultyMemo"
)

$modelList = @($setupModels)
if ($Mode -eq "operational") {
    $modelList += $operationalExtraModels
}

$args = @("manage.py", "dumpdata")
$args += $modelList
$args += @("--indent", "2", "--output", $temporaryOutputPath)
if ($IncludeNaturalKeys) {
    $args += @("--natural-foreign", "--natural-primary")
}

Write-Host ""
Write-Host "EduGradesPro data export"
Write-Host "Mode      : $Mode"
Write-Host "Output    : $resolvedOutputPath"
Write-Host "Model count: $($modelList.Count)"
Write-Host ""

$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    if (Test-Path -LiteralPath $temporaryOutputPath) {
        Remove-Item -LiteralPath $temporaryOutputPath -Force
    }

    & python @args
    if ($LASTEXITCODE -ne 0) {
        throw "Django dumpdata failed with exit code $LASTEXITCODE."
    }

    $json = [System.IO.File]::ReadAllText($temporaryOutputPath, $strictUtf8NoBom)
    [System.IO.File]::WriteAllText($resolvedOutputPath, $json, $utf8NoBom)
}
finally {
    if ($null -eq $previousPythonUtf8) {
        Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONUTF8 = $previousPythonUtf8
    }

    if ($null -eq $previousPythonIoEncoding) {
        Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }

    if (Test-Path -LiteralPath $temporaryOutputPath) {
        Remove-Item -LiteralPath $temporaryOutputPath -Force
    }
}

Write-Host ""
Write-Host "Export complete: $resolvedOutputPath"
Write-Host "Review the bundle before loading it into staging or production."

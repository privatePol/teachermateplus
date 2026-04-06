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
$args += @("--indent", "2", "--output", $OutputPath)
if ($IncludeNaturalKeys) {
    $args += @("--natural-foreign", "--natural-primary")
}

Write-Host ""
Write-Host "EduGradesPro data export"
Write-Host "Mode      : $Mode"
Write-Host "Output    : $OutputPath"
Write-Host "Model count: $($modelList.Count)"
Write-Host ""

& python @args

Write-Host ""
Write-Host "Export complete: $OutputPath"
Write-Host "Review the bundle before loading it into staging or production."

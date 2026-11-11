import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
STATUS_SCRIPT = SCRIPTS / "status_tainan_assessment_tasks.ps1"
TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def _decode_output(data: bytes) -> str:
    """解码 PowerShell 子进程输出：UTF-8 优先，GBK/GB18030 控制台回退。"""
    for enc in ("utf-8", "gb18030"):
        try:
            text = data.decode(enc)
            if "\ufffd" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _run_powershell_file(path: Path, args: list[str] | None = None):
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    if args:
        command.extend(args)
    result = subprocess.run(
        command,
        capture_output=True,
        cwd=str(PROJECT_ROOT),
        timeout=120,
    )
    return (
        result.returncode,
        _decode_output(result.stdout),
        _decode_output(result.stderr),
    )


def _run_status_with_mock(tmp_path: Path, *, task9, task22, info) -> tuple[int, str, str]:
    """在 Mock Get-ScheduledTask/Get-ScheduledTaskInfo 下运行真实 status 脚本。"""
    harness = tmp_path / "status_harness.ps1"
    lines = [
        f". '{STATUS_SCRIPT.as_posix()}' -SkipMain",
        f"$script:task9 = {task9}",
        f"$script:task22 = {task22}",
        f"$script:taskInfo = {info}",
        "$missing = Show-TainanTaskStatuses -Tasks @($script:task9, $script:task22) -Infos @($script:taskInfo, $script:taskInfo) -Names @('Taiwan Election Assessment - Day 9', 'Taiwan Election Assessment - Day 22')",
        "if ($missing -gt 0) { exit 1 }",
        "exit 0",
    ]
    harness.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _run_powershell_file(harness)


def _task(state: str = "Ready", execute: str = "cmd.exe") -> str:
    return (
        "[pscustomobject]@{ State = '" + state + "'; Actions = @("
        "[pscustomobject]@{ Execute = '" + execute + "'; Arguments = '/c x'; "
        "WorkingDirectory = 'C:\\proj' } ) }"
    )


def _info(last_result: int = 0, has_next: bool = True, last_run: bool = True) -> str:
    return (
        "[pscustomobject]@{ LastRunTime = "
        + ("[datetime]'2026-08-09 09:00:00'" if last_run else "$null")
        + "; LastTaskResult = " + str(last_result)
        + "; NextRunTime = " + ("[datetime]'2026-08-22 09:00:00'" if has_next else "$null")
        + " }"
    )


def _parse_task_xml(path: Path) -> ET.Element:
    """解析 install 脚本导出的任务 XML（UTF-16），返回根元素。"""
    return ET.parse(str(path)).getroot()


def _task_xml_find(root: ET.Element, path: str) -> ET.Element:
    """按命名空间前缀逐段定位 XML 节点。"""
    return root.find("/".join(TASK_NS + part for part in path.split("/")))


class TestSchedulerScripts:
    def test_install_contains_both_tasks(self):
        text = _read("install_tainan_assessment_tasks.ps1")
        assert "Taiwan Election Assessment - Day 9" in text
        assert "Taiwan Election Assessment - Day 22" in text
        assert "DaysOfMonth" in text
        assert "ScheduleByMonth" in text
        assert "New-ScheduledTaskTrigger -Monthly" not in text

    def test_install_default_run_time_0900(self):
        text = _read("install_tainan_assessment_tasks.ps1")
        assert '[string]$RunTime = "09:00"' in text

    def test_install_uses_unified_bat(self):
        text = _read("install_tainan_assessment_tasks.ps1")
        assert "run_tainan_assessment.bat" in text

    def test_install_dry_run_guard(self):
        text = _read("install_tainan_assessment_tasks.ps1")
        assert "if (-not $DryRun)" in text
        assert "Register-ScheduledTask" in text

    def test_install_no_old_run_days(self):
        text = _read("install_tainan_assessment_tasks.ps1")
        assert "DaysOfMonth 1" not in text
        assert "DaysOfMonth 16" not in text

    def test_uninstall_only_project_tasks(self):
        text = _read("uninstall_tainan_assessment_tasks.ps1")
        assert text.count("Taiwan Election Assessment") == 2
        assert "Taiwan News Monitor" not in text
        assert "Unregister-ScheduledTask" in text

    def test_status_script_fields(self):
        text = _read("status_tainan_assessment_tasks.ps1")
        for field in ("状态", "上次运行", "上次退出码", "下次运行", "执行命令"):
            assert field in text

    def test_run_now_script_parameters(self):
        text = _read("run_tainan_assessment_now.ps1")
        for param in ("-Mode", "-AsOf", "-PeriodStart", "-PeriodEnd", "-AllowDraftWithGap"):
            assert param in text

    def test_bat_forwards_exit_code(self):
        text = _read("run_tainan_assessment.bat")
        assert "exit /b %ERRORLEVEL%" in text

    def test_runner_uses_python_module(self):
        text = _read("run_tainan_assessment.ps1")
        assert "app.assessment.run_assessment_pipeline" in text
        assert "pipeline_scheduler.log" in text

    def test_install_dry_run_does_not_register(self):
        code, stdout, stderr = _run_powershell_file(
            SCRIPTS / "install_tainan_assessment_tasks.ps1", ["-DryRun"]
        )
        assert code == 0, stderr
        assert "未注册任何计划任务" in stdout

    # ---- 环境无关的 status 解析测试（全部使用 Mock，不访问真实任务计划程序） ----

    def test_status_both_missing(self, tmp_path):
        code, stdout, _ = _run_status_with_mock(
            tmp_path, task9="$null", task22="$null", info=_info()
        )
        assert code == 1
        assert stdout.count("任务不存在") == 2

    def test_status_both_present_enabled(self, tmp_path):
        code, stdout, _ = _run_status_with_mock(
            tmp_path, task9=_task(), task22=_task(), info=_info()
        )
        assert code == 0
        assert "状态：Ready" in stdout
        assert "上次退出码：0" in stdout

    def test_status_mixed_presence(self, tmp_path):
        code, stdout, _ = _run_status_with_mock(
            tmp_path, task9=_task(), task22="$null", info=_info()
        )
        assert code == 1
        assert stdout.count("任务不存在") == 1
        assert "状态：Ready" in stdout

    def test_status_disabled_task(self, tmp_path):
        code, stdout, _ = _run_status_with_mock(
            tmp_path,
            task9=_task(state="Disabled"),
            task22=_task(),
            info=_info(),
        )
        assert code == 0
        assert "状态：Disabled" in stdout

    def test_status_nonzero_last_result(self, tmp_path):
        _, stdout, _ = _run_status_with_mock(
            tmp_path,
            task9=_task(),
            task22=_task(),
            info=_info(last_result=1),
        )
        assert "上次退出码：1" in stdout

    def test_status_next_run_time(self, tmp_path):
        _, stdout, _ = _run_status_with_mock(
            tmp_path,
            task9=_task(),
            task22=_task(),
            info=_info(has_next=True),
        )
        assert "2026-08-22 09:00:00" in stdout

    def test_status_command_path_output(self, tmp_path):
        _, stdout, _ = _run_status_with_mock(
            tmp_path,
            task9=_task(execute="C:\\bad\\path\\run.bat"),
            task22=_task(),
            info=_info(),
        )
        assert "C:\\bad\\path\\run.bat" in stdout

    def test_test_task_names_not_in_production_scripts(self):
        for name in ("install_tainan_assessment_tasks.ps1", "uninstall_tainan_assessment_tasks.ps1", "status_tainan_assessment_tasks.ps1"):
            assert "TEST" not in _read(name)

    def test_skip_main_guard_before_task_query(self):
        text = STATUS_SCRIPT.read_text(encoding="utf-8")
        assert text.index("$SkipMain") < text.index("Get-ScheduledTask")

    def test_harness_uses_mock_objects_only(self, tmp_path):
        # 状态解析 harness 只注入内存对象，不查询真实计划任务
        harness = tmp_path / "harness_check.ps1"
        harness.write_text(
            "function Get-ScheduledTask { throw 'REAL TASK SCHEDULER ACCESS' }\n"
            f". '{STATUS_SCRIPT.as_posix()}' -SkipMain\n"
            "$null = Get-TainanTaskStatusText -Tasks @($null) -Infos @($null) -Names @('X')\n"
            "Write-Output 'ok'\n",
            encoding="utf-8",
        )
        code, stdout, stderr = _run_powershell_file(harness)
        assert code == 0, stderr
        assert "ok" in stdout


class TestR2AssessmentInstallScript:
    """Tainan Election Assessment（install_r2_assessment_task.ps1）安装脚本。

    只运行 -DryRun / -ExportXmlOnly 路径，绝不注册/修改真实计划任务。
    """

    INSTALL = SCRIPTS / "install_r2_assessment_task.ps1"

    def test_default_logon_mode_is_noninteractive_s4u(self):
        text = _read("install_r2_assessment_task.ps1")
        assert '[string]$LogonMode = "NonInteractive"' in text
        assert '$logonType = "S4U"' in text
        assert "<LogonType>$logonType</LogonType>" in text
        assert "$logonType = \"InteractiveToken\"" in text  # 交互模式作为显式 fallback 保留

    def test_no_password_material(self):
        text = _read("install_r2_assessment_task.ps1")
        assert "-Password" not in text
        assert "-rp" not in text
        assert "ConvertTo-SecureString" not in text
        assert "Register-ScheduledTask -User" not in text
        assert "无密码" in text

    def test_project_dir_resolved_relative_to_script(self):
        text = _read("install_r2_assessment_task.ps1")
        assert "Split-Path -Parent $PSScriptRoot" in text

    def test_export_xml_default_noninteractive(self, tmp_path):
        out = tmp_path / "noninteractive.xml"
        code, stdout, stderr = _run_powershell_file(self.INSTALL, ["-ExportXmlOnly", str(out)])
        assert code == 0, stderr
        assert out.exists()
        root = _parse_task_xml(out)
        assert _task_xml_find(root, "Principals/Principal/LogonType").text == "S4U"

    def test_export_xml_interactive_fallback(self, tmp_path):
        out = tmp_path / "interactive.xml"
        code, stdout, stderr = _run_powershell_file(
            self.INSTALL, ["-ExportXmlOnly", str(out), "-LogonMode", "Interactive"]
        )
        assert code == 0, stderr
        assert out.exists()
        root = _parse_task_xml(out)
        assert _task_xml_find(root, "Principals/Principal/LogonType").text == "InteractiveToken"

    def test_export_xml_semantics_preserved(self, tmp_path):
        out = tmp_path / "semantics.xml"
        code, stdout, stderr = _run_powershell_file(self.INSTALL, ["-ExportXmlOnly", str(out)])
        assert code == 0, stderr
        root = _parse_task_xml(out)
        days = [d.text for d in root.iter(TASK_NS + "Day")]
        assert days == ["9", "22"]  # 每月 9/22 日
        wd = _task_xml_find(root, "Actions/Exec/WorkingDirectory").text
        assert wd == str(PROJECT_ROOT)  # 指向当前主目录
        args = _task_xml_find(root, "Actions/Exec/Arguments").text
        assert args.startswith("/d /c call")
        assert "run_assessment_scheduled.bat" in args
        assert _task_xml_find(root, "Actions/Exec/Command").text == "cmd.exe"
        assert _task_xml_find(root, "Settings/ExecutionTimeLimit").text == "PT2H"
        assert _task_xml_find(root, "Settings/MultipleInstancesPolicy").text == "IgnoreNew"
        assert _task_xml_find(root, "Settings/StartWhenAvailable").text == "true"
        assert _task_xml_find(root, "Settings/UseUnifiedSchedulingEngine").text == "true"

    def test_post_register_verification_guard(self):
        text = _read("install_r2_assessment_task.ps1")
        assert "Register-ScheduledTask" in text
        assert "注册后验证失败" in text  # 注册后校验 action 指向当前主目录

    def test_dry_run_does_not_register(self):
        code, stdout, stderr = _run_powershell_file(self.INSTALL, ["-DryRun"])
        assert code == 0, stderr
        assert "DryRun" in stdout

    def test_no_probe_test_names_in_production_script(self):
        text = _read("install_r2_assessment_task.ps1")
        assert "probe" not in text.lower()
        assert "PROBE" not in text.upper()

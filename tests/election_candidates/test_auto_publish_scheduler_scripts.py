import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
INSTALL = SCRIPTS / "install_auto_publish_task.ps1"
TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
TASK_NAME = "Tainan Election Fact Auto Publisher"


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def _decode_output(data: bytes) -> str:
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


def _parse_task_xml(path: Path) -> ET.Element:
    return ET.parse(str(path)).getroot()


def _task_xml_find(root: ET.Element, path: str) -> ET.Element:
    return root.find("/".join(TASK_NS + part for part in path.split("/")))


class TestAutoPublishTaskInstallScript:
    """Tainan Election Fact Auto Publisher 安装脚本。

    只运行 -DryRun / -ExportXmlOnly 路径，绝不注册/修改真实计划任务。
    """

    def test_default_logon_mode_is_noninteractive_s4u(self):
        text = _read("install_auto_publish_task.ps1")
        assert '[string]$LogonMode = "NonInteractive"' in text
        assert '$logonType = "S4U"' in text
        assert "<LogonType>$logonType</LogonType>" in text
        assert '$logonType = "InteractiveToken"' in text  # 交互模式作为显式 fallback 保留

    def test_no_password_material(self):
        text = _read("install_auto_publish_task.ps1")
        assert "-Password" not in text
        assert "-rp" not in text
        assert "ConvertTo-SecureString" not in text
        assert "Register-ScheduledTask -User" not in text
        assert "无密码" in text

    def test_project_dir_resolved_relative_to_script(self):
        text = _read("install_auto_publish_task.ps1")
        assert "Split-Path -Parent $PSScriptRoot" in text

    def test_task_name_and_stagger_minute(self):
        for name in ("install_auto_publish_task.ps1", "status_auto_publish_task.ps1",
                     "uninstall_auto_publish_task.ps1"):
            assert TASK_NAME in _read(name)
        text = _read("install_auto_publish_task.ps1")
        assert "($monitorMinute + 10) % 30" in text  # monitor :19/:49 -> :29/:59 错峰

    def test_export_xml_default_noninteractive(self, tmp_path):
        out = tmp_path / "noninteractive.xml"
        code, stdout, stderr = _run_powershell_file(INSTALL, ["-ExportXmlOnly", str(out)])
        assert code == 0, stderr
        assert out.exists()
        root = _parse_task_xml(out)
        assert _task_xml_find(root, "Principals/Principal/LogonType").text == "S4U"

    def test_export_xml_interactive_fallback(self, tmp_path):
        out = tmp_path / "interactive.xml"
        code, stdout, stderr = _run_powershell_file(
            INSTALL, ["-ExportXmlOnly", str(out), "-LogonMode", "Interactive"]
        )
        assert code == 0, stderr
        assert out.exists()
        root = _parse_task_xml(out)
        assert _task_xml_find(root, "Principals/Principal/LogonType").text == "InteractiveToken"

    def test_export_xml_semantics_preserved(self, tmp_path):
        out = tmp_path / "semantics.xml"
        code, stdout, stderr = _run_powershell_file(INSTALL, ["-ExportXmlOnly", str(out)])
        assert code == 0, stderr
        root = _parse_task_xml(out)
        # 触发：每 30 分钟，monitor 后 10 分钟（:29/:59）
        trigger = _task_xml_find(root, "Triggers/TimeTrigger")
        start = trigger.find(TASK_NS + "StartBoundary").text
        minute = int(start[14:16])
        assert minute % 30 == 29, start
        rep = trigger.find(TASK_NS + "Repetition")
        assert rep.find(TASK_NS + "Interval").text == "PT30M"
        assert rep.find(TASK_NS + "StopAtDurationEnd").text == "false"
        wd = _task_xml_find(root, "Actions/Exec/WorkingDirectory").text
        assert wd == str(PROJECT_ROOT)  # 指向当前主目录
        args = _task_xml_find(root, "Actions/Exec/Arguments").text
        assert args.startswith("/d /c call")
        assert "run_auto_publish_candidates.bat" in args
        assert _task_xml_find(root, "Actions/Exec/Command").text == "cmd.exe"
        assert _task_xml_find(root, "Settings/ExecutionTimeLimit").text == "PT30M"
        assert _task_xml_find(root, "Settings/MultipleInstancesPolicy").text == "IgnoreNew"
        assert _task_xml_find(root, "Settings/StartWhenAvailable").text == "true"
        assert _task_xml_find(root, "Settings/UseUnifiedSchedulingEngine").text == "true"
        assert _task_xml_find(root, "Settings/Enabled").text == "true"
        assert _task_xml_find(root, "Principals/Principal/RunLevel").text == "LeastPrivilege"

    def test_post_register_verification_guard(self):
        text = _read("install_auto_publish_task.ps1")
        assert "Register-ScheduledTask" in text
        assert "注册后验证失败" in text  # 注册后校验 action 指向当前主目录

    def test_s4u_rejection_never_falls_back_to_interactive(self):
        text = _read("install_auto_publish_task.ps1")
        assert "S4U（Passwordless）注册被拒绝" in text
        assert "不会回退到交互模式冒充成功" in text
        assert "以管理员身份运行" in text
        # catch 分支里只有提示 + throw，绝无再次 Register 交互任务
        tail = text.split("catch {")[-1]
        assert "Register-ScheduledTask" not in tail

    def test_dry_run_does_not_register(self):
        code, stdout, stderr = _run_powershell_file(INSTALL, ["-DryRun"])
        assert code == 0, stderr
        assert "DryRun" in stdout
        assert "未注册任务" in stdout

    def test_no_probe_test_names_in_production_script(self):
        text = _read("install_auto_publish_task.ps1")
        assert "probe" not in text.lower()
        assert "PROBE" not in text.upper()


class TestAutoPublishRunnerScripts:
    def test_bat_forwards_exit_code(self):
        text = (PROJECT_ROOT / "run_auto_publish_candidates.bat").read_text(encoding="utf-8")
        assert "exit /b %ERRORLEVEL%" in text

    def test_bat_uses_python_module_and_append_log(self):
        text = (PROJECT_ROOT / "run_auto_publish_candidates.bat").read_text(encoding="utf-8")
        assert "app.election_candidates.auto_review_orchestrator" in text
        assert "--config config/election_candidate_pipeline.yaml" in text
        assert "auto_publish_candidates.log" in text
        assert ">>" in text  # 日志 append

    def test_status_script_fields(self):
        text = _read("status_auto_publish_task.ps1")
        for field in ("状态", "上次运行", "上次退出码", "下次运行", "执行命令", "工作目录"):
            assert field in text

    def test_uninstall_requires_force(self):
        text = _read("uninstall_auto_publish_task.ps1")
        assert "-Force" in text
        assert "Unregister-ScheduledTask" in text

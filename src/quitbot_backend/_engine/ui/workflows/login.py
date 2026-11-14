"""QQ's saved-account / QR pages, independent of the desktop provider."""

import base64
import hashlib
import re
import subprocess
import time

from ....discovery import read_window_profile, scan_windows
from ...errors import ControlUnavailable
from ...locking import ui_transaction
from ...platforms import automation, close_automation, initialize_thread
from ..backend import wait_until
from .inspection import require_unlocked


class QQLoginDriver:
    def __init__(self):
        self.window = None
        self.process = None
        self.owned_processes = []
        self.expected_account = None
        self.profile = None
        self.submitted_at = None
        self.started_at = None
        self.pid = None
        self.new_instance = False
        self.previous_pids = set()

    def _launch(self, windows, *, new_instance):
        from ...platforms.launch import qq_command

        process = subprocess.Popen(
            qq_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.process = process
        self.owned_processes.append(process)
        self.started_at = time.monotonic()
        self.new_instance = new_instance
        self.previous_pids = {w.pid for w in windows}
        self.window = self.pid = self.profile = self.expected_account = None
        self.submitted_at = None

    def start(self, hwnd, pid, *, new_instance=False):
        with ui_transaction():
            if (hwnd is None) != (pid is None):
                raise ValueError("请选择完整的 QQ 进程窗口")
            if new_instance and hwnd is not None:
                raise ValueError("新开登录窗口时无需选择已有 QQ")
            windows = scan_windows()
            if new_instance:
                # Reopening an unfinished flow reuses its login window. Once
                # logged in, a new request starts another native QQ process.
                if (
                    self.new_instance
                    and self.profile is None
                    and (
                        self.window is not None
                        and self.window in windows
                        or self.window is None
                        and self.started_at is not None
                        and time.monotonic() - self.started_at < 60
                    )
                ):
                    pending = self.read()
                    if pending["state"] != "logged_in":
                        return pending
                self._launch(windows, new_instance=True)
                return self.read()
            if hwnd is not None:
                windows = [w for w in windows if (w.hwnd, w.pid) == (hwnd, pid)]
                if len(windows) != 1:
                    raise ValueError("QQ 窗口已变化，请刷新后重试")
            if len(windows) > 1:
                raise ValueError("发现多个 QQ 窗口，请选择要登录的进程")
            if not windows:
                if (
                    self.process is None
                    or self.process.poll() is not None
                    or self.window is not None
                    or self.started_at is not None
                    and time.monotonic() - self.started_at >= 60
                ):
                    self._launch(windows, new_instance=False)
                self.window = None
            else:
                self.window = windows[0]
                self.new_instance = False
                self.previous_pids.clear()
                if (
                    self.pid == self.window.pid
                    and self.profile is None
                    and self.submitted_at is not None
                    and time.monotonic() - self.submitted_at < 30
                ):
                    return self.read()
            self.pid = self.window.pid if self.window else None
            self.expected_account = None
            self.profile = None
            self.submitted_at = None
            return self.read()

    def _window(self):
        windows = scan_windows()
        if self.window is None:
            if self.pid is not None:
                windows = [w for w in windows if w.pid == self.pid]
            elif self.new_instance:
                windows = [w for w in windows if w.pid not in self.previous_pids]
                launched = [w for w in windows if w.pid == self.process.pid]
                if launched:
                    windows = launched
            if len(windows) == 1:
                self.window = windows[0]
                self.pid = self.window.pid
            elif len(windows) > 1:
                raise ValueError("出现多个 QQ 窗口，请重新选择进程")
        elif self.window not in windows:
            # QQ replaces its login HWND after authentication. Keep the same PID.
            same = [w for w in windows if w.pid == self.window.pid]
            self.window = same[0] if len(same) == 1 else None
            if self.window is None:
                self.profile = None
                return None
        return self.window

    @staticmethod
    def _walk(auto, root):
        return [root, *(c for c, _ in auto.WalkControl(root, maxDepth=30))]

    @staticmethod
    def _class(nodes, name):
        return [c for c in nodes if name in c.ClassName.split()]

    @staticmethod
    def _invoke(auto, controls):
        if len(controls) != 1 or not controls[0].IsEnabled or controls[0].IsOffscreen:
            raise ValueError("QQ 登录控件已变化，请刷新登录状态")
        control = controls[0]
        pattern = control.GetPattern(auto.PatternId.InvokePattern)
        if pattern:
            pattern.Invoke(waitTime=0.1)
            return
        selection = control.GetPattern(auto.PatternId.SelectionItemPattern)
        if selection:
            selection.Select(waitTime=0.1)
            return
        legacy = control.GetLegacyIAccessiblePattern()
        if legacy and legacy.DefaultAction:
            legacy.DoDefaultAction(waitTime=0.1)
            return
        raise ValueError("当前 QQ 登录控件没有可用的操作接口")

    def _login_nodes(self, auto, root):
        nodes = self._walk(auto, root)
        single = self._class(nodes, "single-login")
        if single and not self._class(nodes, "account-list-item"):
            names = self._class(self._walk(auto, single[0]), "name")
            self._invoke(auto, names)
            try:
                nodes = wait_until(
                    lambda: self._walk(auto, root),
                    lambda value: bool(self._class(value, "account-list-item")),
                    timeout=3,
                )
            except ControlUnavailable as error:
                raise ValueError(
                    "QQ 账号列表未展开，请读取当前登录状态后重试"
                ) from error
        return nodes

    def _accounts(self, auto, nodes):
        def text(control):
            # QQ nests the account popup inside the current nickname control.
            # Its labels and numbers describe choices, not the selected account.
            if control.ControlTypeName in ("MenuControl", "MenuItemControl"):
                return ""
            if control.ControlTypeName == "TextControl":
                return control.Name
            return "".join(text(child) for child in control.GetChildren())

        single = self._class(nodes, "single-login")
        current = self._class(self._walk(auto, single[0]), "name") if single else []
        current_name = text(current[0]) if current else None
        result = []
        for c in self._class(nodes, "avatar-item") + self._class(
            nodes, "account-list-item"
        ):
            if "add-account-btn" in c.ClassName.split():
                continue
            children = self._walk(auto, c)
            numbers = {
                match[1]
                for n in children
                if (
                    match := re.fullmatch(
                        r"QQ\s*(?:号|号码)[:：]\s*(\d{5,12})", n.Name.strip()
                    )
                )
            }
            numbers.update(
                text(n).strip()
                for n in self._class(children, "uin")
                if re.fullmatch(r"[1-9]\d{4,11}", text(n).strip())
            )
            number = numbers.pop() if len(numbers) == 1 else None
            names = self._class(children, "avatar-name") or self._class(
                children, "name"
            )
            nickname = text(names[0]) if names else c.Name
            if not nickname.strip():
                continue
            key = hashlib.sha256(
                (c.Name + "\0" + nickname + "\0" + (number or "")).encode()
            ).hexdigest()
            result.append(
                (
                    c,
                    {
                        "key": key,
                        "account_id": number,
                        "nickname": nickname,
                        "selected": "is-selected" in c.ClassName.split()
                        or "account-list-item" in c.ClassName.split()
                        and nickname == current_name,
                    },
                )
            )
        if len({a["key"] for _, a in result}) != len(result):
            raise ValueError("QQ 保存了同名且无法区分的账号，请使用二维码登录")
        if sum(a["selected"] for _, a in result) > 1:
            for _, account in result:
                account["selected"] = False
        return result

    def _operate(self, action=None, account_key=None):
        window = self._window()
        if window is None:
            starting = self.started_at and time.monotonic() - self.started_at < 60
            return {
                "state": "starting" if starting else "closed",
                "message": "正在启动 QQ…" if starting else "QQ 已关闭，可重新启动",
            }
        if self.profile is not None and action is None:
            return {
                "state": "logged_in",
                "profile": self.profile,
                "message": "QQ 已登录，请连接账号",
            }
        auto = automation()
        require_unlocked(auto)
        root = auto.ControlFromHandle(window.hwnd)
        if root.ProcessId != window.pid:
            raise ValueError("QQ 进程身份已变化")
        root.SetActive(waitTime=0.1)
        nodes = self._login_nodes(auto, root)
        if self._class(nodes, "user-avatar"):
            if action is not None:
                raise ValueError("QQ 已登录，请读取登录状态后连接账号")
            profile = read_window_profile(window)
            if not profile.available:
                raise ValueError("QQ 已登录，账号资料尚未加载完成")
            if self.expected_account and profile.account_id != self.expected_account:
                raise ValueError("实际登录的 QQ 号与所选账号不同，请重新选择")
            self.profile = profile
            return {
                "state": "logged_in",
                "profile": profile,
                "message": "QQ 已登录，请连接账号",
            }
        existing = [
            c
            for c in nodes
            if c.ControlTypeName == "ButtonControl" and c.Name == "打开QQ"
        ]
        if existing:
            if action == "open_existing" and account_key is None:
                self._invoke(auto, existing)
                # QQ can close a second login process and reveal the original
                # logged-in process. Only this explicit native handoff permits
                # selecting another PID; its account is verified before binding.
                self.window = None
                self.pid = None
                self.profile = None
                self.new_instance = False
                self.previous_pids.clear()
                self.started_at = time.monotonic()
                return {"state": "waiting", "message": "正在打开已登录的 QQ…"}
            if action is not None:
                raise ValueError("此账号已在后台登录，请打开已登录的 QQ")
            return {
                "state": "existing",
                "message": "此账号已在后台登录，可直接打开并连接",
            }
        if action == "open_existing":
            raise ValueError("QQ 的打开提示已变化，请读取当前状态")
        # QR login remains usable when saved nicknames are ambiguous.
        if action == "qr":
            if account_key is not None:
                raise ValueError("扫码操作无需账号选择键")
            if self._class(nodes, "qr-code-img"):
                raise ValueError("二维码已显示，请读取当前登录状态")
            scan = [
                c
                for c in nodes
                if c.ControlTypeName == "ButtonControl" and c.Name == "扫码登录"
            ]
            if scan:
                self._invoke(auto, scan)
            else:
                self._invoke(
                    auto,
                    self._class(nodes, "add-account-btn")
                    or self._class(nodes, "add-user"),
                )
                # The single-account layout first opens its account/password
                # page; use its explicit QR button without entering credentials.
                next_nodes = wait_until(
                    lambda: self._walk(auto, root),
                    lambda value: bool(
                        self._class(value, "qr-code-img")
                        or self._class(value, "password-container")
                    ),
                    timeout=5,
                )
                if self._class(next_nodes, "password-container"):
                    self._invoke(
                        auto,
                        [
                            c
                            for c in next_nodes
                            if c.ControlTypeName == "ButtonControl"
                            and c.Name == "扫码登录"
                        ],
                    )
            self.expected_account = None
            self.submitted_at = None
            return {"state": "waiting", "message": "正在打开二维码…"}
        try:
            accounts = self._accounts(auto, nodes)
        except ValueError:
            if action is not None:
                raise
            return {
                "state": "accounts",
                "message": "保存的账号无法唯一识别，请使用二维码登录",
            }
        qr = self._class(nodes, "qr-code-img")
        expired = self._class(nodes, "expired-btn")
        back = self._class(nodes, "login-main__back-btn")
        if action is not None:
            if action == "login":
                if (
                    self.submitted_at is not None
                    and time.monotonic() - self.submitted_at < 30
                ):
                    raise ValueError("登录已提交，请等待 QQ 返回结果")
                selected = [(c, a) for c, a in accounts if a["key"] == account_key]
                if len(selected) != 1:
                    raise ValueError("所选账号不在当前 QQ 登录页")
                if not selected[0][1]["selected"]:
                    self._invoke(auto, [selected[0][0]])

                def selected_account():
                    fresh = self._accounts(auto, self._login_nodes(auto, root))
                    return [a["key"] for _, a in fresh if a["selected"]] == [
                        account_key
                    ]

                try:
                    wait_until(selected_account, timeout=3)
                except ControlUnavailable as error:
                    raise ValueError(
                        "QQ 尚未确认所选账号，请重新读取登录状态或使用扫码登录"
                    ) from error
                # Close the account selector before pressing the login button.
                fresh_nodes = self._walk(auto, root)
                single = self._class(fresh_nodes, "single-login")
                if single and self._class(fresh_nodes, "account-list-item"):
                    name = self._class(self._walk(auto, single[0]), "name")[0]
                    self._invoke(auto, [name])
                self.expected_account = selected[0][1]["account_id"]
                self._invoke(auto, self._class(self._walk(auto, root), "login-btn"))
                self.submitted_at = time.monotonic()
                return {
                    "state": "waiting",
                    "message": "已提交登录，请按手机 QQ 提示确认",
                }
            if account_key is not None:
                raise ValueError("此操作无需账号号码")
            if action == "refresh_qr":
                self._invoke(auto, expired)
            elif action == "back":
                self._invoke(auto, back)
            self.submitted_at = None
            return {"state": "waiting", "message": "正在更新 QQ 登录页…"}
        if expired:
            return {
                "state": "expired",
                "can_go_back": bool(back),
                "message": "二维码已过期，请刷新",
            }
        if qr:
            if len(qr) != 1 or qr[0].IsOffscreen:
                raise ValueError("QQ 二维码尚未显示完整")
            rect = qr[0].BoundingRectangle
            if not 80 <= rect.width() <= 512 or not 80 <= rect.height() <= 512:
                raise ValueError("QQ 二维码尺寸异常，请刷新登录状态")
            data = qr[0].ToBitmap().ToBytes("png")
            return {
                "state": "qr",
                "qr_image": "data:image/png;base64,"
                + base64.b64encode(data).decode("ascii"),
                "can_go_back": bool(back),
                "message": "请使用手机 QQ 扫码并确认登录",
            }
        if accounts:
            if (
                self.submitted_at is not None
                and time.monotonic() - self.submitted_at < 30
            ):
                return {
                    "state": "waiting",
                    "message": "正在等待 QQ 登录，请查看手机确认提示",
                }
            return {
                "state": "accounts",
                "accounts": [a for _, a in accounts],
                "message": "选择已保存账号，或使用二维码登录",
            }
        if self._class(nodes, "password-container"):
            return {"state": "accounts", "message": "请选择扫码登录，使用手机 QQ 确认"}
        return {
            "state": "waiting",
            "can_go_back": bool(back),
            "message": "正在等待 QQ 登录，请查看手机确认提示",
        }

    def _transaction(self, action=None, account_key=None):
        initialized = initialize_thread()
        try:
            with ui_transaction():
                return self._operate(action, account_key)
        finally:
            close_automation(initialized)

    def read(self):
        return self._transaction()

    def act(self, action, account_key):
        return self._transaction(action, account_key)

    def close(self):
        for process in reversed(self.owned_processes):
            if process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

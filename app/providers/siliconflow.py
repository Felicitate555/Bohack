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


def response_error(response):
    status = response.status_code
    advice = {
        400: "请求参数或模型不受支持，请检查所选模型和参数。",
        401: "API Key 无效或未生效，请检查配置并重启服务。",
        402: "账户无法使用该服务，请检查硅基流动账户。",
        403: "账户无权使用该模型，请检查模型权限和实名认证。",
        404: "接口或模型不存在，请检查模型名称和服务地址。",
        429: "请求达到限额或频率限制，请稍后重试。",
        500: "模型服务出错，请稍后重试。",
        503: "模型服务暂时不可用，请稍后重试。",
        504: "模型服务处理超时，请稍后重试。",
    }.get(status, "请检查模型和服务状态。")
    trace = response.headers.get("x-siliconcloud-trace-id", "")
    trace = trace if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", trace) else ""
    return ProviderError(f"硅基流动接口返回 HTTP {status}：{advice}" + (f" 请求编号：{trace}" if trace else ""))


def post(path, payload, timeout=120, binary=False):
    key = api_key()
    if not key:
        raise ProviderError("请在 .env 中填写 SILICONFLOW_API_KEY 并重启。")
    try:
        response = requests.post(base_url() + path, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json=payload, timeout=timeout)
    except requests.Timeout as exc:
        raise ProviderError("连接硅基流动超时，请检查网络或稍后重试。") from exc
    except requests.RequestException as exc:
        raise ProviderError("连接硅基流动失败，请检查网络。") from exc
    if not response.ok:
        raise response_error(response)
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
    if role == "director_pro":
        return chat_stream(role, payload)
    body = post("/chat/completions", payload)
    try:
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ProviderError("模型输出达到长度上限，方案被截断，请重试。")
        content = choice["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        raise ProviderError("模型没有返回有效内容。") from exc
    return content, {"role": role, "model": model(role), "usage": body.get("usage", {})}


def stream_lines(response, deadline, max_bytes=8 * 1024 * 1024):
    """Read bounded SSE lines, including checking deadlines on partial lines."""
    pending = bytearray()
    total = 0
    for chunk in response.iter_content(chunk_size=1):
        if time.monotonic() >= deadline:
            raise ProviderError("旗舰导演响应超过总时限，请稍后重试；已保存的成果不会丢失。")
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ProviderError("旗舰导演响应超过大小限制，请重试。")
        pending.extend(chunk)
        if chunk.endswith(b'\n'):
            try:
                yield pending.decode('utf-8').strip()
            except UnicodeDecodeError as exc:
                raise ProviderError("旗舰导演返回的数据流编码错误。") from exc
            pending.clear()
    if pending:
        try:
            yield pending.decode('utf-8').strip()
        except UnicodeDecodeError as exc:
            raise ProviderError("旗舰导演返回的数据流编码错误。") from exc


def chat_stream(role, payload):
    """Read flagship output incrementally; never execute returned content."""
    key = api_key()
    if not key:
        raise ProviderError("请在 .env 中填写 SILICONFLOW_API_KEY 并重启。")
    payload = {**payload, "stream": True}
    pieces, usage, finish_reason, done = [], {}, None, False
    deadline = time.monotonic() + 600
    try:
        with requests.post(base_url() + "/chat/completions", headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json=payload, timeout=(20, 240), stream=True) as response:
            if not response.ok:
                raise response_error(response)
            for line in stream_lines(response, deadline):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError("旗舰导演返回的数据流格式错误。") from exc
                if event.get("error"):
                    raise ProviderError("旗舰导演服务返回错误，请检查模型服务状态。")
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if isinstance(content, str):
                        pieces.append(content)
                    finish_reason = choice.get("finish_reason") or finish_reason
    except requests.Timeout as exc:
        raise ProviderError("旗舰导演等待响应超时，请稍后重试。") from exc
    except requests.RequestException as exc:
        raise ProviderError("旗舰导演连接中断，请检查网络后重试。") from exc
    if finish_reason == "length":
        raise ProviderError("旗舰导演输出达到长度上限，方案被截断。")
    if not done and not finish_reason:
        raise ProviderError("旗舰导演响应中断，未收到完整结果。")
    content = "".join(pieces).strip()
    if not content:
        raise ProviderError("旗舰导演没有返回最终分镜内容。")
    return content, {"role": role, "model": payload["model"], "usage": usage}


def parse_json(content):
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        start = content.find("{")
        try:
            data, _ = json.JSONDecoder().raw_decode(content[start:]) if start >= 0 else (None, 0)
        except json.JSONDecodeError:
            raise ProviderError("模型返回的 JSON 不完整，请重试。") from exc
    if not isinstance(data, dict):
        raise ProviderError("模型返回格式错误，请重试。")
    return data


def speech(text, voice="anna", speed=1.0, role="speech"):
    ident = model(role)
    if voice not in {"alex", "benjamin", "charles", "david", "anna", "bella", "claire", "diana"}:
        raise ProviderError("不支持该预设音色。")
    if ident == "fnlp/MOSS-TTSD-v0.5" and not re.match(r"^\s*\[S[12]\]", text):
        text = "[S1]" + text
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

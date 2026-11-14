"""Generate scene-specific code and run it only in a disposable container."""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from ..providers import siliconflow

IMAGE = 'vibelab-code-renderer:local'
MAX_OUTPUT = 64 * 1024 * 1024
SYSTEM = """你是短片动态图形设计师和 Python 程序员。为当前镜头设计专属、可解释内容的画面，而非使用预设组件。
只输出 Python 代码，不要 Markdown。必须定义 render_frame(t, duration, w, h)，返回大小为 (w,h) 的 PIL.Image。timeline 中的字幕时间和节拍是镜头内的秒数，只有提供时才能使用；节拍为估计值，应让关键画面适当靠近而非牺牲解释清晰度。
可以使用 Pillow、math、random、numpy 和 Python 标准库，字体可用 /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc 或 NotoSerifCJK-Regular.ttc。每帧根据 t 和 duration 独立、确定性绘制；实现视觉层次、排版、过渡和与内容一致的运动。避免用内部变量名作画面文字，避免在画面中绘制字幕：系统会单独叠字幕。不得依赖联网、外部文件、进程、音频或额外素材。请保证运行成本合理。"""


def docker_binary():
    found = shutil.which('docker')
    if found:
        return found
    if os.name == 'nt' and os.getenv('LOCALAPPDATA'):
        installed = Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'DockerDesktop' / 'resources' / 'bin' / 'docker.exe'
        if installed.is_file():
            return str(installed)
    return None


def sandbox_command(name):
    return [docker_binary() or 'docker', 'run', '--rm', '-i', '--name', name, '--network=none',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit=96', '--memory=768m', '--cpus=2', '--user=65534:65534',
            '--tmpfs=/tmp:rw,nosuid,nodev,noexec,mode=1777,size=256m', IMAGE]


def check_available():
    docker = docker_binary()
    if not docker:
        raise RuntimeError('AI 自由动画需要 Docker；当前找不到 docker 命令。')
    try:
        subprocess.run([docker, 'image', 'inspect', IMAGE], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Docker 未运行或自由动画镜像尚未构建，请按 README 完成设置。') from exc


def generate_code(topic, scene, previous=None, error=None, profile=None, timeline=None, sequence=None):
    request = {'topic': topic, 'scene': scene, 'style': profile, 'timeline': timeline, 'sequence': sequence}
    messages = [{'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': json.dumps(request, ensure_ascii=False)}]
    if previous is not None:
        messages += [{'role': 'assistant', 'content': previous},
                     {'role': 'user', 'content': '上一次生成或画面审查存在以下问题，请修复并输出完整代码：' + str(error)[:1500]}]
    code, event = siliconflow.chat('animation', messages, max_tokens=6000)
    code = code.strip()
    if code.startswith('```'):
        code = code.split('\n', 1)[-1]
        if code.rstrip().endswith('```'):
            code = code.rstrip()[:-3].rstrip()
    if not code or len(code) > 60000:
        raise ValueError('模型没有返回可用的镜头代码。')
    return code, event


def improve_brief(topic, scene, instruction, profile=None):
    system = ('你是动态图形导演。根据用户意见优化该镜头的专属画面设计，描述主体、构图、'
              '动作顺序和与口播的关系；不要依赖固定模板，不修改事实或口播。'
              '仅输出 JSON 对象 {"visual_brief":"详细、可绘制的画面设计"}。')
    source = json.dumps({'topic': topic, 'scene': scene, 'request': instruction, 'style': profile}, ensure_ascii=False)
    raw, event = siliconflow.chat('animation', [{'role': 'system', 'content': system},
                                                {'role': 'user', 'content': source}], max_tokens=2500)
    obj = siliconflow.parse_json(raw)
    brief = obj.get('visual_brief') if isinstance(obj, dict) else None
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError('模型未返回有效的画面设计。')
    return brief.strip()[:1800], event


def render_sandbox(payload, timeout=300, check_cancel=lambda: None):
    docker = docker_binary()
    if not docker:
        raise RuntimeError('AI 自由动画需要 Docker；当前找不到 docker 命令。')
    name = 'vibelab-scene-' + uuid.uuid4().hex
    try:
        proc = subprocess.Popen(sandbox_command(name), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise RuntimeError('无法启动 Docker 沙箱。') from exc
    chunks, errors, size = [], [], [0]
    overflow = threading.Event()

    def read_output(stream, dest, limit):
        while True:
            block = stream.read(65536)
            if not block:
                return
            size[0] += len(block)
            if size[0] > limit:
                overflow.set()
                proc.kill()
                return
            dest.append(block)

    def read_errors():
        while True:
            block = proc.stderr.read(4096)
            if not block:
                return
            errors.append(block)
            if sum(map(len, errors)) > 16000:
                errors.pop(0)

    threads = [threading.Thread(target=read_output, args=(proc.stdout, chunks, MAX_OUTPUT), daemon=True),
               threading.Thread(target=read_errors, daemon=True)]
    for thread in threads: thread.start()
    def write_input():
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    try:
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            check_cancel()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('docker run', timeout)
            try: proc.wait(timeout=.5)
            except subprocess.TimeoutExpired: pass
        writer.join(timeout=2)
        for thread in threads: thread.join(timeout=5)
        if overflow.is_set():
            raise RuntimeError('沙箱输出过大，已停止。')
        if proc.returncode:
            raise RuntimeError('沙箱渲染失败：' + b''.join(errors).decode('utf-8', 'replace')[-1800:])
        data = b''.join(chunks)
        if len(data) < 32 or b'ftyp' not in data[:32]:
            raise RuntimeError('沙箱没有返回有效的 MP4。')
        return data
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise RuntimeError('沙箱渲染超时。') from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        try:
            subprocess.run([docker, 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

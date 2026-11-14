"""Trusted harness. Run only inside the disposable, network-disabled container."""
import contextlib
import json
import math
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    data = json.load(sys.stdin)
    width, height, fps = int(data['width']), int(data['height']), int(data['fps'])
    duration = float(data['duration'])
    if (width, height, fps) not in {(960, 540, 15), (1920, 1080, 30)} or not 0 < duration <= 180:
        raise ValueError('invalid video dimensions or duration')
    namespace = {'__name__': '__generated_scene__'}
    with contextlib.redirect_stdout(sys.stderr):
        exec(compile(data['code'], '<generated-scene>', 'exec'), namespace)
    draw_frame = namespace.get('render_frame')
    if not callable(draw_frame):
        raise ValueError('missing render_frame(t, duration, w, h)')
    font_path = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
    if not Path(font_path).is_file() and Path('C:/Windows/Fonts/msyh.ttc').is_file():
        font_path = 'C:/Windows/Fonts/msyh.ttc'
    font = ImageFont.truetype(font_path, max(20, round(height * .036))) if data.get('captions') else None
    out = str(Path(tempfile.gettempdir()) / 'vibelab-sandbox-scene.mp4')
    command = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
               '-s', f'{width}x{height}', '-r', str(fps), '-i', '-', '-an',
               '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for frame in range(math.ceil(duration * fps)):
            t = frame / fps
            with contextlib.redirect_stdout(sys.stderr):
                image = draw_frame(t, duration, width, height)
            if not isinstance(image, Image.Image) or image.size != (width, height):
                raise ValueError('render_frame must return a PIL image at the requested size')
            image = image.convert('RGB')
            caption = next((c['text'] for c in data.get('captions', []) if c['start'] <= t < c['end']), '')
            if caption:
                draw = ImageDraw.Draw(image)
                draw.text((width // 2, round(height * .89)), caption, font=font, fill='#f5f1e8',
                          anchor='mm', stroke_width=max(1, height // 540), stroke_fill='#161616')
            encoder.stdin.write(image.tobytes())
        encoder.stdin.close()
        error = encoder.stderr.read()
        if encoder.wait() != 0:
            raise RuntimeError('ffmpeg: ' + error.decode('utf-8', 'replace')[-1000:])
    except Exception:
        encoder.kill()
        encoder.wait()
        raise
    sys.stdout.buffer.write(Path(out).read_bytes())


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

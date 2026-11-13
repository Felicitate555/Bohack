import asyncio
from ..providers import siliconflow
from ..providers.model_registry import model


def create(scenes, outdir, provider, voice, speed, update, report, indices=None):
    indices = list(range(len(scenes))) if indices is None else list(indices)
    if provider == "edge":
        import edge_tts
        async def run():
            for i in indices:
                scene = scenes[i]
                update(f"正在生成配音 {i+1}/{len(scenes)}")
                edge_voice = {"anna": "zh-CN-XiaoxiaoNeural", "claire": "zh-CN-XiaoyiNeural", "diana": "zh-CN-XiaoxiaoNeural", "alex": "zh-CN-YunxiNeural", "benjamin": "zh-CN-YunyangNeural"}.get(voice, "zh-CN-XiaoxiaoNeural")
                rate = f"{(float(speed) - 1) * 100:+.0f}%"
                await edge_tts.Communicate(scene["narration"], edge_voice, rate=rate).save(str(outdir / f"voice_{i}.mp3"))
        asyncio.run(run())
        return
    role = "speech_alt" if provider == "moss" else "speech"
    for i in indices:
        scene = scenes[i]
        update(f"正在生成配音 {i+1}/{len(scenes)}")
        try:
            data = siliconflow.speech(scene["narration"], voice, speed, role)
        except siliconflow.ProviderError as exc:
            raise siliconflow.ProviderError(f"第 {i+1} 个镜头配音失败：{exc}") from exc
        (outdir / f"voice_{i}.mp3").write_bytes(data)
        report({"role": "speech", "model": model(role), "utf8_bytes": len(scene["narration"].encode("utf-8")), "status": "complete"})

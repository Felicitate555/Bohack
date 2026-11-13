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
                await edge_tts.Communicate(scene["narration"], "zh-CN-XiaoxiaoNeural").save(str(outdir / f"voice_{i}.mp3"))
        asyncio.run(run())
        return
    role = "speech_alt" if provider == "moss" else "speech"
    for i in indices:
        scene = scenes[i]
        update(f"正在生成配音 {i+1}/{len(scenes)}")
        data = siliconflow.speech(scene["narration"], voice, speed, role)
        (outdir / f"voice_{i}.mp3").write_bytes(data)
        report({"role": "speech", "model": model(role), "utf8_bytes": len(scene["narration"].encode("utf-8")), "actual_cny": None, "status": "complete"})

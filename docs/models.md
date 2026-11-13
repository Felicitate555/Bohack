# 硅基流动模型与媒体流程

在项目根目录 `.env` 中填写 `SILICONFLOW_API_KEY`。默认模型及角色集中在 `app/providers/model_registry.py`，可用 `.env` 覆盖。普通界面只有三种模式，高级模型 ID 通过 `.env` 更换，避免将私钥返回给浏览器。

| 功能 | 默认角色 | 接口 |
| --- | --- | --- |
| 知识编剧 | DeepSeek-V4.1-Flash | `chat/completions` |
| 视觉导演 | GLM-5.3-Flash；导演级为 GLM-5.3 | `chat/completions` |
| 镜头优化 | Kimi-K2.7-Code，输出受控 SceneSpec | `chat/completions` |
| 关键帧审查 | GLM-5.3-Flash | `chat/completions` 图文输入 |
| 图片 | MOSS-Image | `images/generations` |
| 视频 | MiniMax-H3 | `video/submit`、`video/status` |
| 配音 | CosyVoice2，可选 MOSS-TTSD | `audio/speech` |

供应商模型和参数可能变化，请以平台可用模型及官方文档为准。尚未使用真实 API 验证账号权限和模型输出；模型列表连接成功也不代表每个模型可用。

极速使用单模型规划并优先本地动画；智能使用编剧和导演；导演级使用高质量导演并允许一段短视频。图片、视频只有用户勾选且预算和单价都满足时才调用，数学、公式、概率和图表优先程序化动画。用户可在镜头上调整素材类型和描述。成功素材保存于当前项目目录，短视频的音轨不会进入最终音轨。MOSS 生成素材默认遵循服务商返回的水印行为。

`SF_PRICE_IMAGE_CNY` 是一张图片的估算单价，`SF_PRICE_VIDEO_SECOND_CNY` 是视频每秒估算单价，均需用户按当前价格自行填写；留空时跳过对应生成。界面媒体预算**不包括**文本模型、语音及视觉审查，也不是服务商的强制消费上限。记录保留模型、token、字节、请求与用户单价估算，实际账单金额未知时显示未知；平台账单为准。视频请求提交后保存 requestId，重试优先查询旧任务；若响应中断且不知是否扣费，会暂停重试，需用户核查后主动重置素材状态。

单镜头预览不会修改整条已导出视频。AI 优化修改当前镜头的结构化数据；视觉审查读取最近单镜头预览的 3 张静帧，静帧不能替代全视频审查。随后再次点击整片生成，可根据镜头内容、配音和节拍等缓存条件重用未变化的片段；改变镜头时长可能使后续镜头的节拍变化，相关片段会重算。当前不执行模型生成的任意代码，也没有自动反复修复与多用户隔离。

主要实现：`providers/siliconflow.py` 调用供应商接口；`agents/` 实现编剧、镜头优化和视觉审查；`services/media_router.py` 负责素材决策；`services/media_assets.py` 负责限额和本地素材；`services/speech.py` 负责配音；`browser_render.py` 负责合成与镜头缓存。知识脚本和视觉导演规划均保存在本地项目 JSON 中，模型与 token 用量在工作台显示。所有模型调用都是按实际操作触发；本次开发使用模拟响应，不自动发起付费请求。

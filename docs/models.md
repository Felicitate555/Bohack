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

极速使用单模型规划，手动选择 AI 图片时使用 image_fast 模型；智能使用编剧和导演，图片使用 image 模型；导演级使用编剧、旗舰导演与动画导演，并可选择视频模型。音频服务由用户明确选择，不随模式悄悄切换。勾选素材生成后，程序按镜头选择和描述请求图片或视频，数学、公式、概率和图表优先程序化动画。素材保存于当前项目目录，短视频音轨不会进入最终音轨。

文本模型的 token 数以接口返回为准；图片、视频和语音不一定返回 token。视频提交后保存 requestId，重试优先查询旧任务；若请求结果不明确会暂停重复提交，需核查后主动重置素材状态。

单镜头预览不会修改整条已导出视频。AI 优化修改当前镜头的结构化数据；视觉审查读取最近单镜头预览的 3 张静帧，静帧不能替代全视频审查。随后再次点击整片生成，可根据镜头内容、配音和节拍等缓存条件重用未变化的片段；改变镜头时长可能使后续镜头的节拍变化，相关片段会重算。当前不执行模型生成的任意代码，也没有自动反复修复与多用户隔离。

主要实现：`providers/siliconflow.py` 调用供应商接口；`agents/` 实现编剧、镜头优化和视觉审查；`services/media_router.py` 负责素材决策；`services/media_assets.py` 负责素材生成与存储；`services/speech.py` 负责配音；`browser_render.py` 负责合成与镜头缓存。知识脚本和视觉导演规划均保存在本地项目 JSON 中，接口返回的文本 token 用量在工作台显示，未返回的调用不计入该数字。所有模型调用都是按实际操作触发；本次开发使用模拟响应，不自动发起真实模型请求。

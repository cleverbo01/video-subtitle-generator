# 视频字幕生成器

用于本地批量转录视频/音频，并生成原声版、中外版和目标语言字幕的 Windows 图形工具。


## 首次使用

1. 双击 `启动环境配置助手.vbs`。
2. 在图形界面选择自己的 Whisper-GPT 文件夹和 FFmpeg。
3. 按需测试 API，并保存配置。
4. 双击 `启动双语字幕工具.vbs` 开始使用。
<img width="2544" height="1257" alt="image" src="https://github.com/user-attachments/assets/ea7d214b-3105-4b07-9d79-feac57ce1c68" />

发布包不包含模型、视频、字幕、日志、缓存、API Key 或任何用户配置。


## 默认设置

- 内容预设：普通电影
- 字幕内容：完整转录（包含语气声）
- 性能模式：均衡（推荐）
- 设备：GPU
- API：空白，需用户自行配置
程序支持 CUDA 不可用时自动使用 CPU int8。具体环境状态请使用“首次配置与环境检测”查看。


## 隐私与文件安全

- API Key 默认不保存；只有用户明确勾选后才保存到本机。
- 视频整理功能复制原始媒体，不移动输入目录文件。
- 配置保存在程序旁的 `project_config.json`，该文件不应提交到 GitHub。


## 核心想解决的问题是：

- 在线找字幕经常会遇到版本对不上、时间轴飘、片源太冷门等问题，寻找费时费力
- 本地 NAS 、课程视频、访谈、录屏没有现成字幕，只能自己生成，
- 一些在线语音大模型平台存在审核，导致有些音频质量差、无法生成，且算力收费
- 本地生成针对特定目标进行预设特调，识别率和体验更加


## 目标用户

- 普通电影：本地收藏的外语电影没有中文字幕，例如 4K 蓝光片源只有英文（外语）音轨。
- 动画片：动画对白快、角色多，想保留原声并生成双语字幕。
- 动作片：爆炸、枪声、低语和快速对白混在一起，希望尽量不漏掉关键台词。
- 网络长视频：本地文件没有可用字幕，且不希望把原始视频上传到云端。
- 网课：英语课程、技术讲座、公开课，转录后生成中英双语字幕，方便回看和检索。
互联网短视频：访谈、播客切片、测评、口播视频，快速生成字幕或翻译字幕。


## 设计逻辑就是：

视频 / 音频文件（媒体文件夹）
↓ FFmpeg / FFprobe 读取与探测
本地 Whisper 转录（GPU、CPU检测并调用）
↓ 清理、质量检查、时间轴修复（py脚本本地处理）
原声字幕SRT
↓ 可选：LLM 上下文翻译（自己接入API）
中外双语 SRT / 目标语言 SRT
↓ 可选：整
视频 + 字幕 + 转录文本同名文件夹
简单来讲就是：
用本地 GPU / CPU、FFmpeg 和 Whisper 把视频或音频转成原文字幕；
需要翻译时，再把字幕文本交给兼容 OpenAI API 的模型翻译；
输出原声版、中外双语版和目标语言版字幕。


## 针对一些想法和建议的回复：

# 1.potplayer自带字幕翻译功能和寻找字幕功能，为什么还要这个？
potplayer确实有相关功能，但网络字幕不一定可以找到，而且有些视频根本没有字幕，这个小工具的定位是本地视频，有些字幕翻译还要依靠网络实时翻译，暂停快进都会影响体验，且有些用户用的不是potplayer播放器。
所以本工具针对的是有本地化需求、追求更好实际体验的通用用户
<img width="1277" height="1112" alt="image" src="https://github.com/user-attachments/assets/a9b38a57-41b8-4d67-a8ff-5fdeabe5d824" />

# 2.已有成熟项目：WEIFENG2333/VideoCaptioner，有啥区别？
<img width="867" height="732" alt="image" src="https://github.com/user-attachments/assets/695924e5-6c18-45e8-99ce-2e10a2690555" />
该项目确实完成度更高，功能更全面，如果需要通用字幕生产能力（UP主），它是很好的选择
在我的工具制作初期没有看到这个项目，不过确实还是有些定位差异的：
本工具相对更简单，轻量化，基本纯python代码实现，针对特定分类的视频进行了一定程度的参数特调，后期用户可以按照自己的需要让Agent根据需求轻松修改，是个人轻量化可快速适配调整的项目，有更高的二次开发可玩性。
并且对不同需求视频提供了速度、准确性、批量化、API接口的多种选择
<img width="587" height="132" alt="image" src="https://github.com/user-attachments/assets/181eebec-0e9b-4c98-82f1-c56697f6e9fa" />
<img width="1290" height="474" alt="image" src="https://github.com/user-attachments/assets/d5f101b8-515d-415e-ad29-f99ffcf988f9" />
<img width="1125" height="192" alt="image" src="https://github.com/user-attachments/assets/1134a75e-ab90-44f3-a2c3-e5653413a68d" />

# 3.AI也可以做短视频的处理/在线音视频处理平台？
针对在线AI/平台，有些需要上传且有额度和内容限制，上传受到多重影响，对于偶尔使用比较方便，
本工具依托本地算力，不受过多限制，且可拓展性、针对性较强，还可接入其他项目

# 4.翻译效果和隐私怎么取舍，速度如何？
翻译功能依托API，用户自己接入的模型，采用的是openai的通用接口，轻松适配
且针对内容过长、AI回答质量差等问题采取二分法，自动切片处理，优先默认长片段，保证上下文对话翻译精准，符合语境。用户也可以设置等待时间和并发数，有较高的适配性和稳定性。
<img width="678" height="648" alt="image" src="https://github.com/user-attachments/assets/df506b06-f184-4c33-b97a-e8c11f2757bf" />
部分多次未能成功的字幕有特定优化规则，以极小的代价换取速度优势，且每次翻译会继承上次成功字幕数，减少token浪费，翻译中断后可复用缓存，只补译缺失部分。
<img width="1575" height="609" alt="image" src="https://github.com/user-attachments/assets/3706dc4b-05a4-43a1-b195-591a14079d8a" />

# 5.是不是新瓶装旧酒，没啥新的价值？

该项目确实是“旧酒”，但是基于的是本人的另一个较为完整的知识管理系统（有知识生命周期、关系网络、长期演化能力）
现已暂停项目开发，本次是将其中“数据管理层”模块抽离出来，形成一个单独的工具，未来也将作为一个模块接入该系统预留的抽象层接口
我在之前的系统设计/开发过程中，形成了一套针对本地媒体数据的处理思路，现在把“数据管理层”模块独立出来做成一个轻量工具，未来会再通过抽象接口接回知识系统中。
<img width="924" height="687" alt="image" src="https://github.com/user-attachments/assets/23346fda-5fe3-49db-bb54-a145cbd35762" />


## Community Links

  [Linux.Do](https://linux.do) — A community for sharing and discussing technology.


## License

  This project is licensed under the GNU General Public License v3.0.

  See [LICENSE](LICENSE) for details.

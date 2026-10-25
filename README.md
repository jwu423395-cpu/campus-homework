# campus-homework · 校园作业助手

在 Windows 的 Codex 中读取学习通普通作业和模拟考试，让用户选择待办，再求解、复核、填写、保存和提交。过程题可生成含公式与作图的模拟手写页，同步练习支持在练习册原页空白区填写。

这是 **Codex skill + 本地辅助工具**。Codex理解页面、识别题目、推导答案和操作界面；脚本提供输入保护、题单检查、数学核验、排版及进度恢复。不能承诺任意题满分，也不是脱离Codex自动运行的刷题程序。

## 安装

需要 Windows 10/11 交互桌面、Codex、Python 3.11+、Node.js 22+及可登录的学习通客户端/浏览器。

使用Codex的skill-installer从仓库根安装，或克隆到全局skills：

```powershell
$skillRoot = Join-Path $env:USERPROFILE '.codex\skills\campus-homework'
git clone https://github.com/jwu423395-cpu/campus-homework.git $skillRoot
& (Join-Path $skillRoot 'scripts\setup.ps1')
$python = Join-Path $skillRoot '.venv\Scripts\python.exe'
& $python (Join-Path $skillRoot 'scripts\homework.py') doctor
```

自定义CODEX_HOME时放入其skills/campus-homework。脚本只在技能目录创建.venv并安装锁定依赖，不改系统Python或全局npm配置。字体和许可随仓库提供。

下一条Codex消息可调用：

```text
使用 $campus-homework，找出学习通未完成的作业，让我选一下。
使用 $campus-homework，完成大学物理的指定作业，复查后提交。
使用 $campus-homework，完成这些练习册原页，字体F2，按空白区填写。
```

未指定时列待办选一次，指定就定位。已有答案先核对。登录/扫码/验证码需用户处理；只保存或先审阅的要求优先，正常完成所选作业包含复核后提交。

## 字体与纸张

通过聊天编号选择并保存偏好，可说“F2、方格纸、蓝黑色、字大一点”。

| 编号 | 字体 | 特点 |
|---|---|---|
| F1 | 霞鹜文楷 | 默认，规整中文手写风格 |
| F2 | 悠哉字体 | 更轻松的手写风格 |
| F3 | 辰宇落雁体 | 细笔手写，部分简体字用文楷后备并记录 |

P1白纸、P2横线纸（默认）、P3方格纸、P4米黄色笔记纸。支持本地字体、字号、行距、字距、页边距和黑色/蓝黑墨迹。

[样张](assets/samples/README.md)同时含中文、数字、公式和图。可启用 `"handwriting": "natural"`，使中文、公式数字和变量采用所选手写字形，并加入轻微逐字变化；分数、上下标和矩阵位置仍由 MathJax 计算。较自然的组合为 `"font": "chenyu"`、`"handwriting": "natural"`、`"ink_effect": "ballpoint"` 与 `"ink": "#202126"`，让笔墨产生轻微浓淡并与纸面明暗融合。受力/几何/函数图按明确参数绘制，有图区域左图右式，长推导分页。

## 图片与同步练习

- 准确模式：本地可控排版，保留PNG清稿，逐页导出高清JPG上传图（96质量，无色度抽样）。上传文件用短英文名，PDF仅作本地合并预览。
- 拟真模式：Codex内置图像工具生成简洁书桌环境，再程序合成核验纸面，四角完整、正文清晰。无内置工具时可选准确模式，不要求额外API密钥。
- 练习册原页：保留原图，在确认空白区叠加答案；不足加注明页码题号的续页。脚本不自动识别空白区，Codex须先核对并给区域。
- 缺页：确认教材版本、页码题号，检索公开可访问的准确电子版，无法核实则请求补充。

图片为模拟手写/拟真拍照合成效果，保留原图与清稿供核对。参考[凹凸工坊](https://www.autohanding.com/)的选择与预览思路，未使用其网页代码、字体或纸张素材。

## 工具与检查

在仓库根目录运行：

```powershell
& $python scripts/homework.py validate examples/mixed-task.json --reviewed
& $python scripts/homework.py math-check --symbols A w t p -- 'diff(A*cos(w*t+p),t)' '-A*w*sin(w*t+p)'
& $python scripts/homework.py render examples/render-task.json --output output/demo
& $python -m unittest discover -s tests -v
```

validate检查题单和复核记录，math-check辅助受限符号运算，都不能独立证明所有答案正确。上传仍需核对平台实际附件及预览。

- [题单与进度](references/task-format.md)
- [Windows操作](references/windows.md)
- [公式、作图与原页渲染](references/rendering.md)
- [解题与复核](references/solving.md)
- [同步练习](references/workbook.md)
- [实际验证与支持范围](docs/VALIDATION.md)

一个操作代理控制鼠标键盘，求解复核可并行。失焦、窗口变化或截图过期阻止输入；同窗口题号/编辑框仍由Codex核对。答案改动后重新读回保存，提交不明先看平台结果。

## 数据与许可

偏好、题照、草稿、日志仅保存在本机，默认不进入Git。截图针对选定窗口，不公开真实账号、作业记录、Cookie或令牌。

代码采用[MIT](LICENSE)；字体保留原OFL、来源、版本和SHA，见[字体清单](assets/fonts/README.md)。示例题与测试夹具自编，发行包不含真实课程或个人作业资料。

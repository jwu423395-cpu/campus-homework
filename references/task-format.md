# 题单、选项映射与进度

完整自编样例见 [mixed-task.json](../examples/mixed-task.json) 和 [render-task.json](../examples/render-task.json)。样例复核标记仅对应自编题，不能复制为真实作业复核证据。

## 题单 v1

- `schema_version`: `1`。
- `assignment`: `id`、`title`、`course`、`kind` (`homework`/`mock_exam`)。
- `questions`: `id`、`number`、`type`、完整`stem`、`source`、`solution`、`review`。
- `source.complete`: 文字、公式、选项和题图完整可读才为true；另记来源类型、页码、证据路径和链接。
- `type`: `single`、`multiple`、`judgment`、`fill`、`calculation`、`short`。
- 选择题`options`: `id`、`label`、`text`，图片选项另记`image_sha256`；id表示内容，label仅为提取时字母。
- `solution.final`: 选择`{"option_ids":["content-id"]}`；判断`{"value":true}`；填空`{"values":["第一空","第二空"]}`，另需`blank_count`；计算/简答`{"text":"...","tex":"..."}`，至少一项。
- `solution.steps`: `{"text":"说明"}`或`{"tex":"公式"}`数组。`figures`见[渲染接口](rendering.md)。
- `review`: `status:verified`、真实`reviewer`、独立复核`method`，以及`checks`的`conditions`、`answer`、`symbols`、`units`、`language`全true；未完成用pending。
- `delivery`: `native`默认，或`image`。图片先注册最终附件。

`validate --reviewed`检查记录，不证明数学正确。图片由Codex读取，脚本不假装自动OCR全题。

## 当前选项

最新页面所有可见选项写为`[{"label":"A","text":"..."},...]`，运行：

```powershell
& $python scripts/homework.py options task.json q1 live-options.json
```

比较完整集合并保留负号、单位、数学符号，输出当前标签。内容变化或重复即停止，不能只看旧字母。

## 运行日志

默认保存在本地应用数据目录`campus-homework/runs`，亦可指定私有目录。仅一个操作代理写入。

```powershell
& $python scripts/homework.py run-new task.json --path private/run.json
& $python scripts/homework.py run-show private/run.json
& $python scripts/homework.py run-update private/run.json reviewed-task.json
& $python scripts/homework.py run-artifacts private/run.json q5 private/page-001.jpg
& $python scripts/homework.py run-filled private/run.json q1 private/actual.json --evidence private/readback.png
& $python scripts/homework.py run-saved private/run.json --evidence private/saved.png --message '页面显示的保存结果'
& $python scripts/homework.py run-gate private/run.json
# 紧接最终提交动作前记录意图：
& $python scripts/homework.py run-begin private/run.json
# 看到平台提交结果与时间后：
& $python scripts/homework.py run-submitted private/run.json --evidence private/submitted.png --message '页面显示的提交结果' --time '页面显示的提交时间'
```

actual.json与solution.final格式一致。图片题另需`uploaded_sha256`，按附件顺序排列，表示操作代理已核对文件、平台数量、名称、顺序与预览，不是服务器校验API。

更新答案使变化题读回失效。网络不明时submitting禁止重复提交；平台已提交则记录，明确未提交才`run-reconcile --evidence ... --message ...`并重新核实保存。

任务摘要、附件哈希及原子写入检测意外变化。日志是操作账本，不是可信服务端成绩证明；不能创建空截图或虚构结果满足字段。

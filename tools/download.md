# 树树工具箱 - 下载素材

## 触发语示例

- 中文：「把这个视频下载下来，留作个人学习。」
- 中文：「只保存这条链接的音频。」
- English: "Download this video for my personal study archive."
- English: "Save audio only from this link."

## 前置

- 仅处理用户有权访问、下载和个人学习的公开素材。
- 遵守来源平台的服务条款、版权规则和所在地法律。
- 在仓库根目录运行命令；先完成 `config.json` 配置。
- 首次使用先执行环境自检：

```bash
python3 scripts/doctor.py
```

- 自检 exit 0 才继续；exit 1 时按屏幕中的当前平台指引修复。
- 与用户确认下载模式：完整视频，或仅音频。
- 与用户确认素材目录，例如 `$HOME/ShushuStudy/2026-07-15-example`。
- 不把受限、会员专享或地区不可用内容伪装成下载成功。

## 步骤

1. 设置来源链接和本次素材目录；把示例值替换为真实值：

```bash
URL='https://example.com/video'
ITEM_DIR="$HOME/ShushuStudy/2026-07-15-example"
mkdir -p "$ITEM_DIR"
```

2. 下载完整视频时，按用户需要设置最高垂直分辨率：

```bash
python3 scripts/fetch.py video "$URL" --quality 1080 --out "$ITEM_DIR"
```

3. 完整视频命令的分支处理：

- exit 0：报告 `video.mp4` 与 `meta.json`，再检查两者确实存在。
- exit 1：原样概述人话错误；检查网络、链接权限、地区或 MP4 格式。
- exit 2：命令参数错误；核对 `video URL --quality 正整数 --out DIR`。
- 其他 exit code：停止流水线并报告，不猜测产物已经完成。

4. 用户只要音频时，运行：

```bash
python3 scripts/fetch.py audio "$URL" --out "$ITEM_DIR"
```

5. 仅音频命令的分支处理：

- exit 0：报告 `audio.m4a`，并检查文件可读且不是空文件。
- exit 1：报告下载失败原因；不要用其他来源或外部服务静默替代。
- exit 2：核对 `audio URL --out DIR`，修正参数后再运行。
- 其他 exit code：停止并把退出码告诉用户。

6. 完整视频成功后查看稳定元数据字段：

```bash
python3 -m json.tool "$ITEM_DIR/meta.json"
```

- 只依赖 `url`、`title`、`uploader`、`duration_s`、`date` 五个字段。
- 若 JSON 无法解析，即使视频存在也视为本次完整下载未通过验收。
- 仅音频模式不会生成 `meta.json`，不要声称它应该存在。

7. 最后列出本次目录，向用户回报实际文件而非预期文件：

```bash
ls -lh "$ITEM_DIR"
```

## 产物路径

- 完整视频：`<ITEM_DIR>/video.mp4`
- 完整视频元数据：`<ITEM_DIR>/meta.json`
- 仅音频：`<ITEM_DIR>/audio.m4a`
- 所有产物都应位于同一个 `<output_dir>/<YYYY-MM-DD>-<slug>/` 目录。
- 不把 yt-dlp 的临时文件当成最终产物。

## 常见故障

- `网络连接失败`：检查网络后重试，不连续高频请求来源站点。
- `会员专享`：请用户提供有权访问的公开链接，不绕过访问控制。
- `地区限制`：说明当前网络无法访问，不承诺一定能下载。
- `没有兼容 MP4 格式`：换公开且提供 MP4 兼容流的来源。
- `无法写入输出目录`：检查路径、权限、磁盘空间和同名目录冲突。
- `参数错误`：运行 `python3 scripts/fetch.py --help` 查看真实接口。
- 下载结果只用于用户授权的个人学习；转发或再发布前另行确认版权。

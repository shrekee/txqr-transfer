# txqr-transfer

离线文件二维码传输工具：Go 生成二维码与离线播放器，Python 从录制视频中恢复文件。

**A small offline file-transfer CLI: Go QR sender + Python recorded-video receiver.** This repository defines a new `TXQRT1` protocol, not a compatible replacement for any earlier TXQR implementation.

## 功能与边界

- 二进制文件、可选 gzip、确定的分块与会话身份；每个二维码都携带完整的协议元数据。
- PNG 二维码、原始 payload、manifest、无 CDN 的离线 HTML 播放器。
- MP4/MOV 视频读取；默认 ZXing-C++ 识别，可选择 OpenCV 后端比较。
- 有效分块持久化，跨进程继续、多个同会话视频合并；明确报告缺块，再播放**原有**二维码补传。
- 最终恢复前校验完整长度与 SHA-256；不默认覆盖已有文件。

第一版没有 GUI、网络传输、喷泉码/FEC、旧协议转换或私有平台集成。此项目用于有权限的离线文件传输，不提供规避组织安全控制的功能。二维码的实际可读性受屏幕、拍摄、光照、对焦、压缩等影响；**合成视频测试不等于真实相机可靠性验证，也不构成吞吐量承诺**。

## 安装

验证基线：Linux x86-64、Go 1.27.3、Python 3.12.14。Go 模块最低版本见 `sender-go/go.mod`；Python 包声明的版本范围不等于每个平台都已验证。

```sh
git clone https://github.com/shrekee/txqr-transfer.git
cd txqr-transfer
mkdir -p build
(cd sender-go && go build -buildvcs=false -o ../build/txqr-go .)
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e ./receiver-python --no-deps
./build/txqr-go --help
txqr-restore --help
```

二维码编码使用 `skip2/go-qrcode`；识别使用官方 `zxing-cpp` Python 绑定，OpenCV 负责视频解复用。依赖版本锁定用于可重现的 Python 3.12 wheel/API 验证基线，并非“最新版本”或已完成漏洞审计的保证。OpenCV 采用 4.14.0.94：其[官方发行说明](https://github.com/opencv/opencv-python/releases/tag/4.14.0.94)说明已更新捆绑 FFmpeg 至 8.1.2 以修复 CVE-2026-8461。升级依赖后应重新运行全部测试。第三方许可证及 wheel 内的编解码库说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 传输与恢复

```sh
# 生成一次；输出目录须不存在
./build/txqr-go send ./sample.bin --out ./send-bundle --preset conservative
# 浏览器离线打开 send-bundle/player.html，播放并录制屏幕
# 将录制的视频交给恢复器；output 是目录

txqr-restore ./recording.mp4 --state ./receive-state --output ./restored --report ./scan-report.json
```

`send-bundle/` 包含 `manifest.json`、`payloads/000000.txt`、`qr/000000.png` 和 `player.html`。保留整个目录；补传不能重新压缩或重新分块。同一输入换文件名、压缩字节或分块布局会产生不同会话。接收状态目录只服务**一个**会话；不同会话请使用不同目录。

发送端预设仅是参数组合，不是可靠性保证：`conservative` 为较慢显示/较小分块，`balanced` 居中，`fast` 更激进。优先级为 **显式选项 > 预设 > 默认值**。可用 `--chunk-size`、`--ecc L|M|Q|H`、`--qr-version`、`--scale`、`--pixels`、`--fps`、`--compression none|gzip` 调整。以 CLI `--help` 为最终参数说明。**像素变大只改变显示尺寸，不会增加二维码容量**；发送端使用真实二维码编码器校验包括全部协议和 base64 开销的 payload，容量不足会失败。

## 缺块与补传

缺块退出码为 `2`，报告里的 `missing` 是**从 0 开始**的原始分块索引；`session` 是完整会话 SHA-256。

```sh
./build/txqr-go replay ./send-bundle --missing ./scan-report.json --out ./replay-bundle
# 离线播放 replay-bundle/player.html 并再次录制
# 使用相同 state 继续；也可一次传入多个同会话视频

txqr-restore ./repair.mov --state ./receive-state --output ./restored --report ./scan-report.json
```

补传从保存的 bundle 复制原有 payload 与 PNG，验证 manifest 中的哈希，不会重新生成布局。`--missing 0,3,7` 可显式选择零基索引；使用报告文件更能避免会话选错。恢复成功退出 `0`；错误退出 `1`，不会把不完整文件当作成功。已存在的输出文件默认报错；只有明确传入 `--overwrite` 才允许替换。

## 安全与资源限制

- `TXQRT1` 不提供加密或身份认证。SHA-256 检测损坏，但攻击者也能重新计算它；真实性需要独立可信的摘要/签名。不要将机密直接显示为二维码。
- 原始与传输字节均最多 64 MiB；最多 100000 块；每块 1..4096 字节；编码后的协议帧最多 16384 ASCII 字符。合法协议分块不代表能装入选定 QR 版本/ECC。
- 状态加载、元数据、精确分块长度、重复/冲突、路径名和受限解压都会检查。输出目录不能位于状态目录内部；报告须位于二者之外，且不能覆盖输入文件。混合会话或冲突重复项报错；已接收的有效数据不会因单帧识别异常而丢失。
- 把接收状态目录视为本地可信工作区，避免多个进程同时操作同一状态。不要把状态、输出或报告放在第三方可写的目录中。
- 视频解析涉及本地原生库。协议大小限制不是解码器沙箱；只处理可信录制文件，必要时在操作系统资源限制/隔离环境内运行。不同 OpenCV wheel、容器、系统的 MP4/MOV 编解码器支持可能不同。
- 不要修改 manifest、payload 或 PNG。保留独立可信的原始 SHA-256，并在需要高保障时自行核对恢复文件。

完整格式见 [protocol/SPEC.md](protocol/SPEC.md)，公开合成黄金样本见 [protocol/golden.json](protocol/golden.json)。

## 测试

```sh
. .venv/bin/activate
(cd sender-go && go test ./... && go vet ./...)
python -m pytest receiver-python/tests tests -q
# 单独生成合成视频（不是相机录制质量测试）
python tests/synthetic_video.py ./send-bundle ./synthetic.mp4
```

测试覆盖协议黄金样本、非法/边界输入、恢复状态、重复/乱序/混合会话、错误保留进度，以及真实 Go PNG → MP4/MOV → Python ZXing → 精确文件的合成链路；缺帧测试使用原始 payload/PNG 补传，再在新进程恢复。生成媒体与构建产物不进入版本控制。测试结果与实际已测范围见 [TESTING.md](TESTING.md)。

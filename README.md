# OpsDesk 桌面运维终端 + 手机管控台

一套不需要自建服务器的桌面运维与授权管控方案：

- **桌面端（Windows 10/11）**：巡检、进程/服务、磁盘、网络、日志、远程指令、自动更新。
- **授权锁定**：设备码绑定 + ECDSA 签名授权；**连续 72 小时未联网校验即锁定**。
- **GitHub 私有仓库作为控制总线**：授权、心跳、远程指令、执行结果、更新包全部走仓库，无需服务器。
- **手机 APK 管控台**：查看在线设备、下发运维指令、吊销/恢复授权、上传并发布更新。
- **双重签名**：厂商私钥签授权，手机管理私钥签指令/更新清单，桌面端各自验签。

---

## 1. 目录结构

```
opsdesk/                 桌面端（Python + Tkinter）
  app.py                 入口
  opsdesk/               核心模块
  tools/                 签发授权、管理密钥、发布清单工具
  tests/                 单元测试
  scripts/build_windows.bat
opsdesk-android/         手机管控台（Kotlin + Compose）
ops/                     GitHub 仓库控制总线目录（需提交到仓库）
  licenses/              厂商签发的设备授权 <device_id>.json
  revoked.json           吊销名单
  devices/               桌面端心跳
  commands/<device_id>/  手机下发的指令队列
  results/<device_id>/   桌面端执行结果
  releases/manifest.json 更新清单（管理私钥签名）
  releases/files/        更新包
dist/
  OpsDeskConsole-debug.apk   已构建的手机端 APK
```

> `ops/` 必须放在 GitHub 仓库根目录，路径与桌面端/手机端约定一致。

---

## 2. GitHub 仓库准备

### 2.1 建一个专用私有仓库

1. 新建**私有**仓库（示例：`le1567/-123`）。
2. 把本项目的 `ops/` 目录提交进去，保留空目录占位文件。
3. 生成访问令牌：GitHub → Settings → Developer settings → **Personal access tokens (fine-grained)**。
   - Repository access：只勾选该仓库。
   - Permissions → Contents：**Read and write**（桌面端要写心跳/结果，手机端要写指令/发布包）。
   - 有效期建议 90 天，到期前轮换。
4. 令牌只填在桌面端/手机端设置里，**不要提交到仓库**。

### 2.2 关于「给仓库建一个秘钥」

两种做法，作用不同：

- **部署密钥（Deploy Key）**：仓库 Settings → Deploy keys，用 `ssh-keygen` 生成一对，公钥填 GitHub，私钥只给需要 `git` 操作的构建机。用于 CI/发版机器拉取代码，**不参与授权校验**。
- **控制总线令牌（PAT）**：上面 2.1 的 Contents 读写令牌，桌面端与手机端用它读写 `ops/`。这是运行时真正需要的。

> 注意：PAT 有写权限，但**即使泄露也无法执行任意远程指令**——指令和更新清单必须由手机管理私钥签名，桌面端验签失败直接忽略。

---

## 3. 密钥体系

| 密钥 | 位置 | 用途 |
| --- | --- | --- |
| 厂商私钥 | `.secrets/vendor_private.pem`（仅签发机） | 签发设备授权 |
| 厂商公钥 | 内置在桌面端 `builtin_keys.py` | 验证授权 |
| 手机管理私钥 | Android Keystore（不可导出） | 签远程指令、签更新清单 |
| 手机管理公钥 | 粘贴到桌面端「管理端公钥」 | 验证指令与更新 |

首次部署生成厂商密钥（如果还没有）：

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "opsdesk")
from opsdesk.crypto_utils import generate_keypair
priv, pub = generate_keypair()
open(".secrets/vendor_private.pem","wb").write(priv)
open(".secrets/vendor_public.pem","wb").write(pub)
print(pub.decode())
PY
```

把输出的公钥替换 `opsdesk/opsdesk/builtin_keys.py` 中的 `VENDOR_PUBLIC_KEY_PEM`。
**厂商私钥务必离线保管，不要提交仓库。**

开发/测试用管理密钥：

```bash
python3 opsdesk/tools/gen_admin_key.py
```

正式使用时，改由手机 APK 首次启动生成（私钥留在 Keystore），把手机里显示的 PEM 公钥粘贴到桌面端设置。

---

## 4. 桌面端部署

### 4.1 运行

```bash
cd opsdesk
pip install -r requirements.txt
python app.py                 # 图形界面
python app.py --device-id     # 只打印本机设备码
python app.py --license-state # 查看授权状态
python app.py --sync          # 立即联网校验一次
```

Windows 上打包：

```bat
cd opsdesk
scripts\build_windows.bat
```

或推 `ops-v*` 标签，GitHub Actions 会自动构建 Windows 压缩包。

### 4.2 首次配置

1. 打开「授权与设置」：
   - 仓库：`le1567/-123`
   - 分支：`main`
   - GitHub 令牌：2.1 生成的 PAT
   - 管理端公钥：手机 App「设置 → 管理公钥」复制过来的 PEM
   - 保存设置
2. 复制界面上的**设备码**（`OPS-XXXXXXXX-XXXXXXXX-XXXXXXXX`）。
3. 管理员签发授权并提交到仓库 `ops/licenses/<设备码>.json`。
4. 桌面端点「联网校验」，状态变为 `active`。

### 4.3 锁定规则

- 每 15 分钟联网校验一次授权；锁定时改为 5 分钟。
- **超过 72 小时没有成功联网校验 → `offline_locked`**，主功能不可用。
- 锁定后仅允许：`ping / unlock / sysinfo / license_status / restart_agent`。
- 防时钟回拨：记录历史最大时间，系统时间回退超过 5 分钟判定 `clock_tamper` 并锁定。
- 授权文件本地用 AES-GCM 加密并绑定机器指纹；复制到别的机器解密失败，同样锁定。
- 吊销名单 `ops/revoked.json` 优先于本地授权，联网校验后立即锁定。
- 手机端「远程锁定」下发 `lock` 指令后立即锁定，需管理端 `unlock` 解锁。

---

## 5. 签发授权

```bash
python3 opsdesk/tools/issue_license.py \
  --device OPS-CD858006-EDB877E4-671D2F09 \
  --customer "某某公司" \
  --days 365 \
  --features inspect,remote,update,remote_lock \
  --offline-hours 72
```

输出到 `ops/licenses/<设备码>.json`，提交并推送即可。
到期后续签：重新签发同设备文件覆盖旧文件，桌面端联网后自动续期。

---

## 6. 手机端 APK

安装 `dist/OpsDeskConsole-debug.apk`（Android 8.0+）。

首次启动：

1. 填写仓库 `owner/repo`、分支、PAT。
2. 自动生成管理密钥，复制**公钥 PEM**到桌面端绑定。
3. 进入「设备」查看心跳，点开设备后可执行：
   - 立即体检 / 完整巡检 / 进程列表 / 服务列表 / 网络信息 / 查看日志
   - 临时文件清理（24h 前）
   - 重启服务（输入服务名）、结束进程（PID 或名称）
   - 检查更新 / 立即安装更新
   - 远程锁定 / 解除锁定
   - 吊销 / 恢复授权
4. 「发版」页选择更新包（≤40MB）、填写版本号与说明，上传到 `ops/releases/files/` 并签名生成清单。

指令流程：手机写 `ops/commands/<设备码>/<指令ID>.json`（含管理签名）→ 桌面端 60 秒内轮询 → 验签执行 → 结果写 `ops/results/<设备码>/<指令ID>.json` → 删除指令文件。

修改管理公钥或重新安装 App 会导致密钥变化，需要重新在桌面端绑定公钥；旧指令将因验签失败被忽略。

---

## 7. 发版与升级

方式 A：手机 App「发版」直接上传。

方式 B：命令行：

```bash
python3 opsdesk/tools/sign_release.py \
  --version 1.0.1 \
  --file dist/OpsDesk-1.0.1.zip \
  --notes "修复巡检超时" \
  --key .secrets/admin_private.pem
```

然后：

1. 把 zip 上传到 `ops/releases/files/`。
2. 把 `ops/releases/manifest.json` 提交推送。
3. 桌面端「远程与更新」点检查更新，或手机下发 `update_check` / `apply_update`。

桌面端会校验：管理端签名 → 文件大小 → SHA-256。校验通过后退出程序，用批处理 `robocopy` 覆盖安装目录并重启；失败会保留旧版本。

---

## 8. 构建与测试

桌面端：

```bash
cd opsdesk
python3 -m unittest discover -s tests -v
```

手机端（需要 JDK 17 + Android SDK 34）：

```bash
cd opsdesk-android
export JAVA_HOME=/path/to/jdk17
./gradlew testDebugUnitTest assembleDebug
# 产物：app/build/outputs/apk/debug/app-debug.apk
```

本仓库已包含 Gradle Wrapper。若本机内存较小，`gradle.properties` 已限制 JVM 1.4GB 并使用进程内 Kotlin 编译。

---

## 9. 安全边界与建议

- 厂商私钥、管理私钥不上传仓库；`.secrets/` 已加入 `.gitignore`。
- 桌面端 PAT 用机器指纹派生密钥加密存储，换机后需重填。
- 远程脚本默认关闭（`allow_remote_script=false`），开启后仍要求管理签名。
- 指令有效期 7 天，时间超前 5 分钟视为无效，执行后指令文件删除，防止重放。
- 仓库必须是私有仓库；公开仓库等于把设备心跳、指令结果暴露给所有人。
- 需要更强隔离时，可把 `ops/` 放到独立仓库，仅授予 Contents 权限，并定期轮换 PAT。
- APK 当前使用 debug 签名，正式分发前请用自有 keystore 重新签名（`app/build.gradle.kts` 的 release signingConfig）。

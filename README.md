<h1 align="center"><strong>重要免责声明</strong></h1>

<h2 align="center"><strong>本项目存在较高法律、账号、隐私和车辆安全风险。使用前请完整阅读本声明。</strong></h2>

<p>
  <strong>
    本项目不是 AITO、问界、赛力斯、华为或鸿蒙智行官方项目，也未获得上述品牌、厂商或平台的授权、认可或支持。
    本项目仅用于个人 Home Assistant 集成开发、技术验证和学习研究。车辆属于高风险联网设备，相关数据可能涉及车辆安全、隐私数据、云端服务规则以及辅助驾驶相关场景。
    任何使用、传播、修改或部署行为均由使用者自行判断并承担全部责任。
  </strong>
</p>

<p>
  <strong>
    请勿将本项目用于商业用途、批量调用、绕过限制、未授权账号、未授权车辆、公开服务或任何可能违反法律法规、平台规则、车辆服务条款的用途。
    请勿公开日志、诊断文件、Home Assistant 存储文件、备份、账号信息、车辆信息、位置数据、截图或任何敏感凭据。
  </strong>
</p>

<p>
  <strong>
    使用本项目造成的账号异常、服务中断、车辆数据错误、隐私泄露、车辆相关风险、法律纠纷或任何直接/间接损失，均由使用者自行承担。
    如发现风险、侵权或安全问题，请联系 493355621@qq.com 以便及时处理。不同意以上内容，请不要安装或使用本项目。
  </strong>
</p>

---

<p align="center">
  <img src="custom_components/aito/brand/icon.png" alt="AITO" width="120" />
</p>

<h1 align="center">AITO Home Assistant</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-41BDF5" alt="Home Assistant Custom Integration" />
  <img src="https://img.shields.io/badge/status-experimental-orange" alt="Experimental" />
</p>

## 许可状态

本项目代码和文档以 GNU General Public License v3.0 only（GPL-3.0-only）发布，详见仓库根目录的 `LICENSE` 文件。
第三方品牌、商标、服务名称和图标仍归各自权利人所有；本仓库不授予任何第三方商标、品牌或平台服务相关权利。

## 致谢与上游项目

华为账号登录与会话维护实现参考并使用了 [Lynnette177/AITO-API](https://github.com/Lynnette177/AITO-API) 的实现与研究成果。上游项目的版权归原作者所有；本仓库不主张上游项目代码或研究资料的版权。

## 当前支持车型

- 问界 M8（`SERES-F3`）
- 问界 M5（`SERES-X1`）

## 快速上手

### 1. 安装

将 `custom_components/aito` 复制到 Home Assistant 配置目录：

```text
/config/custom_components/aito
```

复制完成后，重启 Home Assistant。

### 2. 添加集成

在 Home Assistant 中进入：

```text
设置 -> 设备与服务 -> 添加集成 -> AITO
```

如果列表中没有看到 `AITO`，请确认：

- 目录路径是 `/config/custom_components/aito`
- Home Assistant 已经重启
- `manifest.json` 位于 `aito` 目录中

### 3. 完成登录

添加集成时，按页面提示输入：

- 华为账号手机号
- 华为账号密码
- 短信验证码

建议为 Home Assistant 单独准备一个专用华为账号，避免与日常手机 App 的车辆服务会话互相影响。

### 4. 等待初始化

提交短信验证码后，集成会等待账号和车辆凭据建立完成，并在本地保存必要资产后再创建设备和实体。

如果登录失败，请优先检查手机号、密码、短信验证码和账号风控状态。不要公开 Home Assistant 日志、诊断文件或 `.storage` 内的任何文件。

### 5. 查看实体

配置成功后，进入 AITO 设备页面查看自动生成的实体。

实体数量和字段取决于车辆、账号权限以及云端实际返回的数据。README 不承诺固定实体列表，请以 Home Assistant 实际显示为准。

### 6. 调整轮询间隔

默认轮询间隔为 `30` 秒。

可在集成选项中调整轮询间隔。过低的轮询频率可能增加云端服务压力，也可能导致请求失败或账号异常。

## 能耗周期数据与历史行程

`0.4.0` 起，集成会把云端能耗报告中已经确认存在的三个周期暴露为实体：

- `total`：累计平均电耗、累计平均油耗。
- `today`：今日平均电耗、平均油耗、总用电量、总用油量。
- `thisMonth`：本月平均电耗、平均油耗、总用电量、总用油量。

能耗报告最多每小时刷新一次。该接口本身不包含行程，但鸿蒙智行 App 使用独立的只读接口提供行车记录：

```text
GET /vdas/v1/report/day-trip-range
Query: startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
Header: X-Vehicle-Id
```

集成最多每 30 分钟刷新最近 7 日，并新增以下 6 个业务摘要实体：

- 最近一次行程里程、开始时间、时长。
- 今日行驶里程。
- 最近 7 日行驶里程。
- 行程报告更新时间。

首次配置或私有存储尚未覆盖完整一年时，集成会在后台按最多 30 天一个窗口逐批回填最近 365 个自然日。每个窗口成功后立即写入 Home Assistant 私有 `.storage`，即使该窗口没有行程也会记录为已覆盖；Home Assistant 重启或集成重新加载后会从尚未覆盖的日期缺口继续，不需要重新抓取已经保存的完整窗口。回填不会阻塞车辆状态和最近 7 日摘要的首次显示。

另外会创建 2 个只反映本地归档状态的实体：

- 行程历史覆盖天数。
- 行程历史回填进度。

App 返回的日小计和单程摘要还包含平均/最高车速、平均电耗和平均油耗。私有归档只保存这些已确认的脱敏数值字段；未知字段、真实 `tripId`、起终点和轨迹坐标在解析时即被丢弃。完整归档不会进入实体 attributes、Recorder、diagnostics 或日志，Home Assistant 中只有上述 8 个聚合/进度状态可见。

`0.4.1` 补充了直接 HTTP 调用下的顶层行程数组解析，同时保留 App 原生桥接层包装形式的兼容。契约失配日志只输出容器类型和白名单字段名，不输出任何响应值或未知键名。

`0.4.2` 修正 M8 充电遥测：充电状态与 App 一致，取 AC/DC 充电状态中的有效最大值；充电中按实际通道读取 `acChargeCurrent` 或 `dcChargeCurrent`，并用选定电流和充电电压计算功率。预约等待、已接枪未充电、故障、停止和预热不再被错误映射为旧的 `0-4` 枚举；旧车型仍保留通用字段回退。

`0.4.3` 修正 M8 位置实体兼容性：非零且范围合法的坐标即使被云端标记为 `validFlag=0`，也会以 `location_quality=last_known`、`location_stale=true` 的“最后已知位置”语义提供给 Home Assistant；只有 `validFlag=1` 才标记为有效位置。`0,0`、布尔值、非数值和越界坐标仍会被拒绝。实体属性只暴露有效性、隐私开关、卫星数和明确命名的云端观察/载荷时间，不把云端时间宣称为 GPS 定位时间。

行程接口成功但没有记录时，今日和最近 7 日里程为 `0`，最近一次行程相关实体为 `unknown`。接口失败或响应结构发生变化时，集成保留上一次成功值及其旧更新时间，不把失败伪装成新的 `0 km`。

已有“总里程”实体仍可配合 Recorder 或 Utility Meter 作为长期累计里程的独立校验来源；它不能替代单次行程分段。

## Lovelace 卡片（可选）

仓库的 `cards/aito-card.js` 是一个配套的自定义卡片，集中展示电量、油/电续航、综合续航、车内与空调温度、四轮胎压、驻车状态，并提供「立即备车」和「哨兵模式」两个开关。卡片标题取车型名（`sensor.aito_model`）。

### 1. 安装卡片文件

将 `cards/aito-card.js` 复制到 Home Assistant 配置目录的 `www` 下：

```text
/config/www/aito-card.js
```

### 2. 注册为前端资源

在 Home Assistant 中进入：

```text
设置 -> 仪表板 -> 右上角三点 -> 资源 -> 添加资源
```

- URL：`/local/aito-card.js`
- 资源类型：`JavaScript 模块`

（或在 YAML 模式下，于 `lovelace.resources` 中添加同样的 `module` 条目。）

### 3. 添加卡片

编辑仪表板，添加一个「手动卡片」，填入：

```yaml
type: custom:aito-card
```

### 车辆图片

集成在初始化时会从车辆资源包中合成一张该车型的整车图片，写入 `www/aito/car.png`，卡片自动引用（`/local/aito/car.png`），无需手动准备图片。若该图暂不可用，卡片会自动隐藏图片区域。

> 卡片脚本顶部的 `CAR_IMAGE_URL` 带一个版本参数（`?v=2`）用于让浏览器在图片更新后重新加载；如自行替换车辆图片，可自行调整该值。

### 说明

- 卡片读取集成生成的实体（`sensor.aito_*`、`switch.aito_*`）。实体的具体 ID 以你的 Home Assistant 为准；若与卡片默认引用不一致，可重命名实体，或修改卡片脚本顶部的常量。
- 卡片仅依赖 Home Assistant 内置能力，无需额外前端依赖。

## 数据与隐私

本集成运行在用户自己的 Home Assistant 环境中。维护者不会通过本项目主动收集、接收或上传用户的账号、车辆或位置数据。

为完成登录和轮询，本集成会在 Home Assistant 本地保存必要的账号会话信息、访问令牌、刷新令牌、设备标识、车辆标识、车辆状态和位置等数据。请妥善保护 Home Assistant 主机、备份、诊断文件、日志和 `.storage` 目录。

调试能耗或行程功能时，只应记录响应字段结构和脱敏后的类型信息。不要公开完整响应、日期明细、车辆 ID、VIN、位置、Token 或 Home Assistant 存储文件。删除集成配置条目时，对应的私有行程归档会一并删除。

## 本地验证

无需 Home Assistant 运行时即可执行能耗字段契约测试：

```bash
python3.12 -m unittest discover -s tests -v
```

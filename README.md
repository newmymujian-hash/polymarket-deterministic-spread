# 确定性价差与尾盘高概率收益捕获策略
## Deterministic Spread & Tail-End Harvesting Strategy — Polymarket

把 `STRATEGY.md` 中的复合策略写成的可运行代码：**结构性数学套利**（底仓）+ **尾盘极值低风险清扫**（现金流），共用一套不可妥协的风控层。默认纸面运行，实盘被三重闸门锁死（见下）。

### 策略 → 代码对照

| 策略条款 | 实现 |
|---|---|
| 资金分仓 40% 套利 / 50% 尾盘 / 10% 备用金 | `risk.py` `RiskConfig` + `BucketLedger`（合计必须 =100%，否则直接报错） |
| 二元互补套利：YES ask + NO ask < $1（扣费后） | `arbitrage.py` `detect_binary_arb`（按订单簿深度逐档测算可成交量与含费单位成本） |
| 多选项打包套利：Σ Ask < $1 | `arbitrage.py` `detect_basket_arb`（事件级，≥3 个结果市场） |
| 尾盘清扫：结算前 1–24h、价格 0.97–0.995 带 | `tailend.py` `detect_tail_candidate` |
| 改进凯利 + 折半执行 + 单笔 2%–5% 上限 | `kelly.py`（超出 2%–5% 带的参数会被拒绝） |
| 条款精细审核（Rules Are Everything） | `risk.py` `RulesGate`：实盘前市场 slug 必须进 `rules_approved.json`（人工逐字读完规则后添加）；扫描结果统一打 `RULES_REVIEW_REQUIRED` 标签 |
| UMA 争议防范 | `risk.py` 关键词 denylist 先筛掉主观/争议型盘口；重点做客观数据驱动的盘 |
| 套利资金不长期锁定 | `arb_max_days`（默认 30 天）限制套利候选结算期限 |
| 主动离场（Sell into strength） | `tailend.py` `exit_signal`（默认 bid ≥ 0.995 提前卖出释放资金） |
| 手续费模型 | `fees.py`：taker fee ≈ rate × p × (1−p)，rate 取自 Gamma 的 `feeSchedule` |

### 安装（Windows / macOS / Linux 通用）

只需要 Python 3.11+，**零第三方依赖**（只用标准库，行情走 Polymarket 公开 API）。

```bat
:: Windows
py -m venv .venv
.venv\Scripts\activate
```

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
```

不需要 `pip install` 也能跑（`run.py` 已处理路径）；想装成命令也行：`pip install -e .` 后可用 `ds-harvest`。

### 使用

```bash
cp config.example.json config.json        # 按需改资金与阈值
python run.py scan --once                 # 只读扫描一轮，机会写入 logs/opportunities.jsonl
python run.py scan --once --paper         # 同时按真实订单簿深度模拟成交（状态存 data/paper_state.json）
python run.py scan --interval 60          # 每 60 秒一轮
python run.py report                      # 汇总已记录的机会
python run.py check                       # 上线前自检（附录清单的可机检项）
python -m unittest discover -s tests -v   # 跑单元测试

# 急停：仓库根目录放一个 STOP 文件，扫描循环会在一轮内停下；删掉即恢复。
```

参数都在 `config.json`：总资金（默认 $10,000，与策略示例一致）、分仓比例、套利最小净边际（默认扣费后 0.5%）、尾盘价格带、尾盘胜率假设 `tail_assumed_prob`（默认 0.995——这是**操作者的评估参数**，不是行情价；只有你的评估胜率明显高于买入价，Half-Kelly 才会给出仓位）。

### 安全与合规（重要）

- **默认纸面**。扫描只读公开行情，不需要任何密钥。
- **实盘三重闸门**（`live.py`）：① 环境变量 `DS_ENABLE_LIVE=1` 且凭证只放环境变量、永不入库；② 运行时查 Polymarket geoblock——**阿尔伯塔（CA-AB）、BC、安省、魁省目前是 close-only（只能平仓、不能开新仓），前端与 API 都一样**，闸门会直接拒绝启动；③ 市场必须经人工读完 Rules 并登记。
- 实盘下单接口需按 Polymarket 现行统一 SDK（`polymarket-client` / `py-clob-client-v2`）接线：2026-04-28 CLOB V2 切换后旧版 `py-clob-client` 已停用，抵押品已由 USDC.e 换成 pUSD。`live.py` 里留了接线位置和签名类型说明（0=EOA / 1=Proxy / 2=Safe / 3=1271）。
- 私钥即资金：永远不要把私钥、API 凭证写进本仓库或任何聊天/文档；实盘用只放小额的专用热钱包。

### 目录

```
STRATEGY.md            策略原文
src/ds_harvest/
  marketdata.py        Gamma/CLOB 只读行情
  arbitrage.py         二元 + 打包套利检测（深度逐档）
  tailend.py           尾盘候选筛选与离场信号
  kelly.py             凯利/半凯利与仓位上限
  risk.py              分仓、denylist、Rules 闸门
  paper.py             纸面账户（按真实盘口成交、含费）
  scanner.py           扫描编排与机会日志
  features.py          logit 空间特征库（动量/ATR_z/Aroon/盘口不平衡/年化）
  live.py              实盘适配器（三重闸门，默认禁用）
  cli.py               命令行（scan / report / check，STOP 急停 + 心跳）
tests/test_strategy.py 单元测试（29 个用例）
docs/methodology-article.md  方法论文章原文（融合取舍见上）
```

### 与方法论文章的融合说明（2026-10-26 并入）

第二篇方法论文章（原文存 `docs/methodology-article.md`）中合理的部分已融合，取舍如下——**融合的**：

| 文章观点 | 落点 |
|---|---|
| 凯利取分数倍（文取 1/4，策略定 1/2） | `kelly_multiplier` 参数化，默认仍按策略原文 0.5，可在 config 改 0.25 |
| EV = p/c − 1、edge = p − c | `kelly.ev_per_dollar`，尾盘候选输出 `ev_per_dollar` 且按**含费价**评估 |
| 时间即成本：edge 必须年化看 | 套利/尾盘候选都输出 `annualized_return` 与 `days_locked`；`min_arb_annualized` 可设门槛过滤“锁很久赚一点点” |
| 冲击上限：单笔 ≤ 盘口深度 20% | 尾盘仓位新增深度封顶（`max_depth_fraction`） |
| 按当前余额算仓位 | 纸面模式按账户实时权益（现金+持仓标记价）缩放预算 |
| 同一事件合并敞口 | 尾盘新增单事件敞口上限 `max_event_exposure_pct`（默认 10%） |
| 日熔断、开仓前写进代码 | `risk.CircuitBreaker`（默认日亏 2%），纸面循环触线只停新仓 |
| Kill switch + 心跳 | 仓库根放 `STOP` 文件一轮内停机；每轮写 `logs/heartbeat.json` |
| 跳过要记档（四张表） | `logs/skips.jsonl` 记录每个被跳过市场及原因 |
| Fail-closed、幂等 | 缺数据/404 分开计数直接跳过；纸面同 token 不重复建仓 |
| 实盘三重开关 | `live.py` 需 `DS_ENABLE_LIVE=1` **且** `DS_LIVE_CONFIRM=I_UNDERSTAND`，再加 Rules 闸门与 geoblock 检查 |
| logit 空间特征（动量/波动/Aroon/盘口不平衡） | 新增 `features.py` 特征库；盘口不平衡已随尾盘候选输出，其余供后续假设检验使用 |
| 上线前检查清单 | `python run.py check` 一键自检（实测 8/8 通过） |

**没有融合的**（及原因）：

- 文章第 5 节的方向性动量入场规则（ROC/EMA/Aroon 触发买卖）：那是方向投机，与策略原文“不做方向性主观投机”直接冲突，只收了特征数学、没收交易规则。
- 文章的 1/4 凯利作默认值：策略原文明确折半执行，默认值从原文，文章的更保守口径留作参数。
- “设信封后无人值守自动实盘”：与阿尔伯塔 close-only 的地理闸门、以及凭证/实盘操作须逐项授权的既定规则冲突——信封思想已化作 config 上限+三重闸门，自动实盘不启用。
- 公开成交假设挖掘、回测框架（文章第 3/6/7 节）：属于研究阶段工程，不在本执行代码库范围内；其回测陷阱清单已体现在纸面设计中（真实买卖价成交、不用中间价、费用从 API 实时费率取、不写死）。

### 已知边界（v0.1）

- 套利双腿成交风险：纸面按同刻盘口模拟；实盘必须用限价单并做"双腿都成或撤"的处理，`live.py` 接线时实现。
- 打包套利要求事件结果互斥且穷尽，检测结果带 `VERIFY_EXHAUSTIVE_OUTCOMES` 标签，需人工结合 Rules 复核（含 neg-risk 结构）。
- 尾盘"大局已定"无法纯靠价格判断：价格带只是初筛，`assumed_prob` 与 Rules 审核才是决策依据。

/* 原型模拟数据 —— 不连接任何真实后台 */
window.DATA = (function () {
  const TODAY = new Date('2026-11-25T09:00:00+08:00');

  const notes = [
    {
      id: 'n1', customer: '星河纸业', title: '复卷机张力控制异常', status: 'active',
      start: '2026-10-28', updated: '2026-11-24',
      path: '客户售后问题/星河纸业-复卷机张力控制异常.md',
      tags: ['张力', 'CDV3000'],
      body: `## 2026-11-24 2#复卷机提速段张力波动
**现象**：车速超过 800 m/min 后，张力波动约 ±6%，低速段正常。
**排查**：
- 张力传感器零点漂移，重新标定后波动降至 ±2%；
- 卷径计算值与实测卷径相差 4 mm，修正卷径系数；
- 检查 CDV3000 张力锥度参数 P-04.12，按工艺要求改为 68%。
**处理**：更换张力编码器，重新完成静态 / 动态标定。
**结果**：1200 m/min 连续运行 4 小时，张力稳定在 ±1.5%。
**待跟进**：一周后回访，补充高速段波形截图。

## 2026-11-18 现场初查
客户反馈换卷后张力瞬时下坠。现场检查发现编码器联轴器有轻微打滑，记录波形后先做临时紧固，安排备件。
关联手册：高创 CDV3000 用户手册 §7.3 张力控制参数。

## 2026-11-03 电话沟通
了解工况：原纸克重 120–180 g/㎡，最高车速 1200 m/min。要求张力控制精度 ≤ ±2%。`,
      summary: '提速段张力波动，已更换编码器并重标定，等待高速段回访。'
    },
    {
      id: 'n2', customer: '星河纸业', title: '1号机 ACS 变频器通讯中断', status: 'active',
      start: '2026-11-10', updated: '2026-11-17',
      path: '客户售后问题/星河纸业-1号机ACS变频器通讯中断.md',
      tags: ['ACS', 'EtherCAT'],
      body: `## 2026-11-17 通讯恢复记录
**现象**：1号机 ACS 驱动器 EtherCAT 从站偶发掉线，OP 状态闪烁。
**排查**：
- 更换交换机端口后掉线频率下降，但未彻底消失；
- 检查网线屏蔽层，发现动力柜内走线过长，存在干扰；
- 核对 ACS 通讯手册 §6.2，将 PDO 刷新周期由 2 ms 调整为 1 ms。
**处理**：重新布线并加装磁环，通讯恢复稳定。
**待跟进**：观察一周，确认夜班高负载工况无掉线。

## 2026-11-12 故障码记录
驱动器面板报 \`E-07 Bus Error\`，复位后可运行。已导出诊断日志。`,
      summary: 'EtherCAT 偶发掉线，重新布线并调整 PDO 周期，观察中。'
    },
    {
      id: 'n3', customer: '星河纸业', title: '老复卷机 ACS 参数迁移', status: 'archived',
      start: '2026-10-20', updated: '2026-11-09',
      path: '客户售后问题/星河纸业-老复卷机ACS参数迁移.md',
      tags: ['ACS', '参数'],
      body: `## 2026-11-09 迁移完成
将旧 ACS 控制器参数导出后迁移至新机，重点校验张力环 PID 与卷径补偿。试运行 3 天无异常，项目归档。

## 2026-10-24 参数备份
使用 SPiiPlus MMI 完整导出参数表，保留原始配置文件。`,
      summary: '参数迁移与试运行完成，已归档。'
    },
    {
      id: 'n4', customer: '华瑞包装', title: '高速分切机纠偏漂移', status: 'active',
      start: '2026-11-02', updated: '2026-11-12',
      path: '客户售后问题/华瑞包装-高速分切机纠偏漂移.md',
      tags: ['纠偏', '高创'],
      body: `## 2026-11-12 纠偏漂移复现
**现象**：分切机在 350 m/min 以上时，边缘纠偏持续向操作侧漂移 2–3 mm。
**排查**：
- 纠偏传感器镜头有粉尘，清洁后改善但不稳定；
- 检查伺服响应，位置环增益偏低，调整后漂移减小；
- 怀疑材料张力不均导致跑偏，待客户提供来料检测报告。
**待跟进**：等待来料检测结果；如仍漂移，计划更换纠偏控制器。

## 2026-11-04 首次上门
更换纠偏传感器滤光片，低速段恢复正常，高速段问题复现。`,
      summary: '高速段纠偏漂移，等待来料检测，暂未闭环。'
    },
    {
      id: 'n5', customer: '华瑞包装', title: '涂布机伺服报警 E-07', status: 'archived',
      start: '2026-10-15', updated: '2026-10-28',
      path: '客户售后问题/华瑞包装-涂布机伺服报警E-07.md',
      tags: ['高创', '故障码'],
      body: `## 2026-10-28 结案
更换 CDV3000 驱动器风扇，清理散热风道后，连续运行 48 小时无 E-07 过热报警。客户验收签字，归档。

## 2026-10-20 排查
E-07 为驱动器过热保护。测量风扇转速异常，确认风扇轴承磨损。`,
      summary: '驱动器过热报警，更换风扇后结案。'
    },
    {
      id: 'n6', customer: '鑫源食品', title: '枕式包装机切刀位置偏移', status: 'active',
      start: '2026-11-15', updated: '2026-11-21',
      path: '客户售后问题/鑫源食品-枕式包装机切刀位置偏移.md',
      tags: ['同步', '切刀'],
      body: `## 2026-11-21 切刀偏移调整
**现象**：换规格后切刀切断位置偏移 5 mm，包装袋封口不齐。
**排查**：电子凸轮相位偏差；重新做切刀原点回零后恢复。
**处理**：调整凸轮相位并保存配方，3 个规格逐一验证。
**结果**：偏移消除，连续生产 2 小时正常。
**待跟进**：下次换规格时远程确认配方切换是否正常。`,
      summary: '切刀相位偏移，已重新回零并验证三个规格。'
    },
    {
      id: 'n7', customer: '鑫源食品', title: '高创驱动器更换调试', status: 'active',
      start: '2026-10-30', updated: '2026-11-05',
      path: '客户售后问题/鑫源食品-高创驱动器更换调试.md',
      tags: ['高创', 'CDV3000'],
      body: `## 2026-11-05 更换后调试
原驱动器烧毁，更换同型号 CDV3000。导入原参数后，电机出现轻微抖动。
**排查**：编码器分辨率参数不一致，手动改为 23 bit 后正常。
**待跟进**：客户尚未确认长期运行情况，需回访。

## 2026-10-30 到货更换
完成硬件更换与上电检查，母线电压正常。`,
      summary: '驱动器更换完成，编码器参数修正，等待回访确认。'
    },
    {
      id: 'n8', customer: '蓝海新材料', title: '收卷机张力锥度设置', status: 'archived',
      start: '2026-10-24', updated: '2026-11-02',
      path: '客户售后问题/蓝海新材料-收卷机张力锥度设置.md',
      tags: ['张力', '锥度'],
      body: `## 2026-11-02 结案
按材料厚度调整张力锥度曲线，收卷端面平整，无松卷。客户确认后归档。`,
      summary: '张力锥度曲线调整完成，已归档。'
    },
    {
      id: 'n9', customer: '恒信药业', title: '灌装机 Agito 编码器故障', status: 'active',
      start: '2026-11-16', updated: '2026-11-23',
      path: '客户售后问题/恒信药业-灌装机Agito编码器故障.md',
      tags: ['Agito', '编码器'],
      body: `## 2026-11-23 编码器更换
**现象**：灌装机主轴定位偶发偏移，Agito AG-2000 报编码器信号异常。
**排查**：编码器电缆屏蔽接地不良；更换编码器并重新做零点标定。
**结果**：连续运行 8 小时定位正常。
**待跟进**：建议客户检查电缆拖链磨损情况。

## 2026-11-18 现场检测
示波器观察 A/B 相波形，发现高速段有毛刺，判断为电缆干扰。`,
      summary: 'Agito 编码器信号异常，已更换并重新标定。'
    },
    {
      id: 'n10', customer: '恒信药业', title: '贴标机同步失步', status: 'active',
      start: '2026-11-05', updated: '2026-11-15',
      path: '客户售后问题/恒信药业-贴标机同步失步.md',
      tags: ['同步', 'ACS'],
      body: `## 2026-11-15 同步失步排查
**现象**：贴标机与主输送线同步偏差累积，约 20 分钟后标签位置偏移。
**排查**：编码器分频系数设置错误，主轴与从轴比例不一致。
**处理**：修正电子齿轮比，试运行 30 分钟正常。
**待跟进**：需连续生产验证，尚未最终确认。`,
      summary: '电子齿轮比修正，等待连续生产验证。'
    }
  ];

  const manuals = [
    { id: 'm1', name: '高创 CDV3000 系列伺服驱动器用户手册.pdf', brand: '高创', pages: 486,
      status: 'ready', chunks: 1284, size: '28.6 MB', path: 'D:\\手册\\高创\\CDV3000 用户手册.pdf',
      added: '2026-10-12', updated: '2026-11-20', progress: 100 },
    { id: 'm2', name: '高创 CDV3000 通讯手册.pdf', brand: '高创', pages: 214,
      status: 'ready', chunks: 486, size: '9.8 MB', path: 'D:\\手册\\高创\\CDV3000 通讯手册.pdf',
      added: '2026-10-18', updated: '2026-11-18', progress: 100 },
    { id: 'm3', name: 'ACS Motion Control 编程手册 v2.3.pdf', brand: 'ACS', pages: 512,
      status: 'ready', chunks: 1410, size: '31.2 MB', path: 'D:\\手册\\ACS\\Motion Control 编程手册 v2.3.pdf',
      added: '2026-10-25', updated: '2026-11-19', progress: 100 },
    { id: 'm4', name: 'ACS 驱动器故障诊断手册.pdf', brand: 'ACS', pages: 168,
      status: 'update', chunks: 392, size: '7.4 MB', path: 'D:\\手册\\ACS\\驱动器故障诊断手册.pdf',
      added: '2026-10-26', updated: '2026-11-24', progress: 100 },
    { id: 'm5', name: 'Agito 运动控制卡 AG-2000 安装指南.pdf', brand: 'Agito', pages: 96,
      status: 'parsing', chunks: 0, size: '4.1 MB', path: 'D:\\手册\\Agito\\AG-2000 安装指南.pdf',
      added: '2026-11-25', updated: '2026-11-25', progress: 62 },
    { id: 'm6', name: 'Agito 编码器接口说明.pdf', brand: 'Agito', pages: 58,
      status: 'queue', chunks: 0, size: '2.3 MB', path: 'D:\\手册\\Agito\\编码器接口说明.pdf',
      added: '2026-11-25', updated: '2026-11-25', progress: 0 },
    { id: 'm7', name: '高创 CDV3000 调试案例合集.pdf', brand: '高创', pages: 238,
      status: 'failed', chunks: 0, size: '12.9 MB', path: 'D:\\手册\\高创\\CDV3000 调试案例合集.pdf',
      added: '2026-11-22', updated: '2026-11-22', progress: 37, error: 'MinerU 解析超时（第 214 页）' },
    { id: 'm8', name: 'ACS SPiiPlus 旧版手册.pdf', brand: 'ACS', pages: 320,
      status: 'invalid', chunks: 968, size: '18.5 MB', path: 'D:\\手册\\ACS\\SPiiPlus 旧版手册.pdf',
      added: '2026-10-28', updated: '2026-11-23', progress: 100 },
    { id: 'm9', name: '高创 CDV3000 参数速查表.pdf', brand: '高创', pages: 24,
      status: 'ready', chunks: 86, size: '1.6 MB', path: 'D:\\手册\\高创\\参数速查表.pdf',
      added: '2026-11-08', updated: '2026-11-16', progress: 100 }
  ];

  const repos = [
    { id: 'r1', name: 'akai-workbench', path: 'D:\\Code\\akai-workbench', branch: 'main',
      dirty: 3, ahead: 2, behind: 0, pinned: true, hidden: false,
      commit: { msg: '补充复卷机张力调试记录与来源引用', time: '2 小时前', hash: 'a1f3c92' }, scanned: '09:32' },
    { id: 'r2', name: 'after-sales-tools', path: 'D:\\Code\\after-sales-tools', branch: 'feat/manual-queue',
      dirty: 0, ahead: 1, behind: 0, pinned: true, hidden: false,
      commit: { msg: 'feat(queue): 入库任务失败重试', time: '昨天 18:40', hash: '7d20e11' }, scanned: '09:32' },
    { id: 'r3', name: 'plc-samples', path: 'D:\\Work\\PLC\\plc-samples', branch: 'master',
      dirty: 0, ahead: 0, behind: 2, pinned: true, hidden: false,
      commit: { msg: 'chore: 同步 2026Q4 样例', time: '3 天前', hash: 'b8e4a07' }, scanned: '09:32' },
    { id: 'r4', name: 'docs-site', path: 'D:\\Code\\docs-site', branch: 'main',
      dirty: 1, ahead: 0, behind: 0, pinned: false, hidden: false,
      commit: { msg: 'docs: 更新手册目录结构', time: '5 天前', hash: 'c2d9f43' }, scanned: '09:32' },
    { id: 'r5', name: 'old-tools', path: 'D:\\Code\\old-tools', branch: 'main',
      dirty: 0, ahead: 0, behind: 0, pinned: false, hidden: true,
      commit: { msg: 'chore: 归档旧脚本', time: '2 个月前', hash: '9a11b7e' }, scanned: '09:32' }
  ];

  const sources = {
    s_n1: { id: 's_n1', type: 'note', noteId: 'n1', title: '星河纸业 · 复卷机张力控制异常',
      meta: '售后记录 · 2026-11-24', section: '2026-11-24 2#复卷机提速段张力波动',
      snippet: '张力传感器零点漂移，重新标定后波动降至 ±2%；卷径计算值与实测卷径相差 4 mm，修正卷径系数；检查张力锥度参数 P-04.12，按要求改为 68%。' },
    s_n2: { id: 's_n2', type: 'note', noteId: 'n2', title: '星河纸业 · 1号机 ACS 变频器通讯中断',
      meta: '售后记录 · 2026-11-17', section: '2026-11-17 通讯恢复记录',
      snippet: '检查网线屏蔽层，动力柜内走线过长存在干扰；将 PDO 刷新周期由 2 ms 调整为 1 ms；重新布线并加装磁环后通讯恢复稳定。' },
    s_n4: { id: 's_n4', type: 'note', noteId: 'n4', title: '华瑞包装 · 高速分切机纠偏漂移',
      meta: '售后记录 · 2026-11-12', section: '2026-11-12 纠偏漂移复现',
      snippet: '纠偏传感器镜头粉尘，清洁后改善但不稳定；位置环增益偏低，调整后漂移减小；怀疑材料张力不均，等待来料检测报告。' },
    s_n5: { id: 's_n5', type: 'note', noteId: 'n5', title: '华瑞包装 · 涂布机伺服报警 E-07',
      meta: '售后记录 · 2026-10-28', section: '2026-10-28 结案',
      snippet: 'E-07 为驱动器过热保护。测量风扇转速异常，确认风扇轴承磨损；更换风扇并清理风道后，连续运行 48 小时无报警。' },
    s_n9: { id: 's_n9', type: 'note', noteId: 'n9', title: '恒信药业 · 灌装机 Agito 编码器故障',
      meta: '售后记录 · 2026-11-23', section: '2026-11-23 编码器更换',
      snippet: '编码器电缆屏蔽接地不良；更换编码器并重新做零点标定，连续运行 8 小时定位正常；建议检查电缆拖链磨损情况。' },
    s_m1: { id: 's_m1', type: 'manual', manualId: 'm1', title: '高创 CDV3000 用户手册', brand: '高创',
      meta: '手册 · 第 128 页', section: '§7.3 张力控制参数', page: 128,
      snippet: 'P-04.12 张力锥度系数：设定收卷过程中的张力递减比例，范围 0–100%，默认 50%。参数受 P-04.10 卷径补偿模式影响。' },
    s_m1b: { id: 's_m1b', type: 'manual', manualId: 'm1', title: '高创 CDV3000 用户手册', brand: '高创',
      meta: '手册 · 第 130 页', section: '§7.4 卷径补偿', page: 130,
      snippet: '卷径补偿支持外部模拟量输入与内部计算两种模式。采用内部计算时需正确设置 P-04.04 初卷径与 P-04.05 材料厚度。' },
    s_m2: { id: 's_m2', type: 'manual', manualId: 'm2', title: '高创 CDV3000 通讯手册', brand: '高创',
      meta: '手册 · 第 47 页', section: '§5.2 EtherCAT PDO 配置', page: 47,
      snippet: 'PDO 刷新周期建议与主站任务周期一致；干扰环境下可将 Sync 周期由 2 ms 调整至 1 ms，并检查屏蔽层接地。' },
    s_m3: { id: 's_m3', type: 'manual', manualId: 'm3', title: 'ACS Motion Control 编程手册', brand: 'ACS',
      meta: '手册 · 第 89 页', section: '§6.2 通讯参数配置', page: 89,
      snippet: 'SPiiPlus 支持 EtherCAT 与 CANopen。PDO 映射偏移需在初始化阶段一次性配置；修改后必须重启通讯栈。' },
    s_m4: { id: 's_m4', type: 'manual', manualId: 'm4', title: 'ACS 驱动器故障诊断手册', brand: 'ACS',
      meta: '手册 · 第 91 页', section: '§4.4 状态字与故障码', page: 91,
      snippet: 'Bus Error 对应状态字 bit 12。常见原因：从站掉线、PDO 周期不匹配、线缆屏蔽失效。建议采集诊断日志后再复位。' },
    s_m4b: { id: 's_m4b', type: 'manual', manualId: 'm4', title: 'ACS 驱动器故障诊断手册', brand: 'ACS',
      meta: '手册 · 第 93 页', section: '§4.6 E-07 故障处理流程', page: 93,
      snippet: '依次检查：电源模块温度、风扇转速、驱动器散热风道。若面板温度持续超过 75℃，应停机检查。' },
    s_m7: { id: 's_m7', type: 'manual', manualId: 'm7', title: '高创 CDV3000 调试案例合集（未入库）', brand: '高创',
      meta: '手册 · 待入库', section: '解析失败', page: 1,
      snippet: '该文档解析失败，尚未进入知识库，不能参与检索。' }
  };

  const answers = {
    tension: {
      text: `张力在提速段波动，通常同时与**检测信号、卷径计算、伺服响应**三处有关。结合历史记录与手册，建议按以下顺序排查：

1. **先校验张力检测信号**：检查传感器零点与编码器联轴器，排除打滑或零点漂移。星河纸业 11-24 的记录中，更换编码器并重新标定后波动从 ±6% 降到 ±1.5%。{{0}}{{1}}
2. **核对卷径与锥度参数**：实测卷径与计算值偏差会造成高速段张力持续偏大。高创 CDV3000 的张力锥度参数为 P-04.12（默认 50%，现场按工艺改为 68%），卷径补偿模式见 §7.4。{{2}}{{3}}
3. **检查加减速段的伺服响应**：确认位置环 / 速度环增益未因更换硬件而恢复默认。若是 EtherCAT 轴，同时核对 PDO 周期与主站一致。{{4}}
4. **记录波形做对比**：分别在 600 / 800 / 1200 m/min 采集张力曲线，确认波动随速度变化的拐点，再决定是否需要调整机械或参数。

> 当前范围：星河纸业 / 复卷机张力控制异常。若需比较其他厂家的处理方式，可切换到「全部手册」或品牌范围。`,
      sources: ['s_n1', 's_m1', 's_m1b', 's_m2']
    },
    fault: {
      text: `**E-07 在不同厂家中含义不同**，需要区分对象：

- 高创 CDV3000：E-07 为**驱动器过热保护**。华瑞包装的处理记录显示，风扇轴承磨损导致散热不足，更换风扇并清理风道后连续运行 48 小时无报警。{{0}}{{1}}
- ACS 驱动器：面板上的 Bus Error 更接近通讯类故障，状态字 bit 12 置位。故障诊断手册建议先采集诊断日志，再检查从站掉线、PDO 周期与线缆屏蔽。{{2}}

**建议排查顺序**：
1. 记录面板完整故障码与闪烁状态，确认是过热还是通讯；
2. 读取驱动器内部故障日志，区分首次触发与连带报警；
3. 若是过热，检查风扇转速与风道；若是通讯，按 §4.4 检查物理层与 PDO 周期；
4. 更换部件后连续运行至少 4 小时再判定闭环。

> 高创《调试案例合集》目前解析失败、未入库，尚不能参与检索；可在知识库中重试入库。{{3}}`,
      sources: ['s_n5', 's_m4b', 's_m4', 's_m7']
    },
    comm: {
      text: `通讯偶发掉线，优先排查**物理层**，再看**周期与配置**。星河纸业的 ACS EtherCAT 掉线案例与两本手册给出的路径一致：

1. **物理层**：检查网线屏蔽层与动力柜走线，干扰环境下建议加装磁环、缩短走线；更换交换机端口属于临时验证手段。{{0}}
2. **PDO / Sync 周期**：高创通讯手册建议 PDO 刷新周期与主站任务周期一致；现场将 Sync 周期由 2 ms 改为 1 ms 后掉线频率明显下降。{{1}}
3. **配置一致性**：ACS 的 PDO 映射偏移需在初始化阶段一次性配置，修改后必须重启通讯栈，否则可能表现为 OP 状态闪烁。{{2}}
4. **故障码确认**：Bus Error 对应 ACS 状态字 bit 12，建议先导出诊断日志再复位，避免丢失首次故障现场。{{3}}

> 若掉线只发生在夜班高负载时段，还需检查柜内温度与电源纹波。`,
      sources: ['s_n2', 's_m2', 's_m3', 's_m4']
    },
    generic: {
      text: `已在当前范围内找到相关记录与手册章节。综合来看：

- **恒信药业 灌装机 Agito 编码器故障**：信号偶发异常源于电缆屏蔽接地不良，更换编码器并重新做零点标定后连续运行 8 小时正常；建议继续检查拖链磨损。{{0}}
- **高创 CDV3000 手册**给出了张力与卷径相关参数的完整定义，可作为参数调整依据。{{1}}
- **星河纸业 复卷机张力控制异常**最近一次处理（11-24）已把张力稳定在 ±1.5%，可作为相似工况的参考。{{2}}

如需更精确的结论，请补充设备型号、故障码或发生工况；手册类问题建议把检索范围切换到「全部手册」。`,
      sources: ['s_n9', 's_m1', 's_n1']
    }
  };

  const fragments = {
    tension: [
      { sid: 's_n1', text: '张力传感器零点漂移，重新标定后波动降至 ±2%；卷径计算值与实测卷径相差 4 mm，修正卷径系数；检查张力锥度参数 P-04.12，按要求改为 68%。' },
      { sid: 's_m1', text: 'P-04.12 张力锥度系数：设定收卷过程中的张力递减比例，范围 0–100%，默认 50%。参数受 P-04.10 卷径补偿模式影响。' },
      { sid: 's_m1b', text: '卷径补偿支持外部模拟量输入与内部计算两种模式。采用内部计算时需正确设置 P-04.04 初卷径与 P-04.05 材料厚度。' },
      { sid: 's_m2', text: 'PDO 刷新周期建议与主站任务周期一致；干扰环境下可将 Sync 周期由 2 ms 调整至 1 ms，并检查屏蔽层接地。' },
      { sid: 's_n4', text: '怀疑材料张力不均导致跑偏，待客户提供来料检测报告；位置环增益偏低，调整后漂移减小。' }
    ],
    fault: [
      { sid: 's_n5', text: 'E-07 为驱动器过热保护。测量风扇转速异常，确认风扇轴承磨损。更换风扇，清理散热风道后，连续运行 48 小时无 E-07 过热报警。' },
      { sid: 's_m4b', text: '依次检查：电源模块温度、风扇转速、驱动器散热风道。若面板温度持续超过 75℃，应停机检查。' },
      { sid: 's_m4', text: 'Bus Error 对应状态字 bit 12。常见原因：从站掉线、PDO 周期不匹配、线缆屏蔽失效。建议采集诊断日志后再复位。' },
      { sid: 's_m7', text: '该文档解析失败，尚未进入知识库，不能参与检索。' }
    ],
    comm: [
      { sid: 's_n2', text: '检查网线屏蔽层，发现动力柜内走线过长，存在干扰；将 PDO 刷新周期由 2 ms 调整为 1 ms；重新布线并加装磁环。' },
      { sid: 's_m2', text: 'PDO 刷新周期建议与主站任务周期一致；干扰环境下可将 Sync 周期由 2 ms 调整至 1 ms，并检查屏蔽层接地。' },
      { sid: 's_m3', text: 'SPiiPlus 支持 EtherCAT 与 CANopen。PDO 映射偏移需在初始化阶段一次性配置；修改后必须重启通讯栈。' },
      { sid: 's_m4', text: 'Bus Error 对应状态字 bit 12。常见原因：从站掉线、PDO 周期不匹配、线缆屏蔽失效。' }
    ],
    generic: [
      { sid: 's_n9', text: '编码器电缆屏蔽接地不良；更换编码器并重新做零点标定，连续运行 8 小时定位正常；建议检查电缆拖链磨损情况。' },
      { sid: 's_m1', text: 'P-04.12 张力锥度系数：设定收卷过程中的张力递减比例，范围 0–100%，默认 50%。' },
      { sid: 's_n1', text: '1200 m/min 连续运行 4 小时，张力稳定在 ±1.5%。待跟进：一周后回访，补充高速段波形截图。' }
    ]
  };

  const sessions = [
    {
      id: 's1', name: '张力控制排查', updated: '今天 09:42',
      messages: [
        { role: 'user', text: '复卷机张力波动大，提速后不稳定，应该怎么排查？', time: '09:41' },
        { role: 'assistant', text: answers.tension.text, sources: answers.tension.sources, kind: 'tension', time: '09:42' }
      ]
    },
    { id: 's2', name: 'ACS 通讯配置对比', updated: '昨天 16:08', messages: [] },
    { id: 's3', name: 'Agito 编码器报警', updated: '11-21', messages: [] }
  ];

  const compare = {
    title: '高创 vs ACS · 通讯与故障诊断对比',
    columns: [
      { brand: '高创', manual: 'CDV3000 用户手册 / 通讯手册' },
      { brand: 'ACS', manual: 'Motion Control 编程手册 / 故障诊断手册' }
    ],
    rows: [
      { dim: '通讯接口', cells: [
        { text: 'EtherCAT / 模拟量 / RS-485', sid: 's_m1' },
        { text: 'EtherCAT / CANopen', sid: 's_m3' }
      ] },
      { dim: 'PDO 周期建议', cells: [
        { text: '与主站任务周期一致；干扰时 1–2 ms', sid: 's_m2' },
        { text: '初始化一次性配置，修改后需重启通讯栈', sid: 's_m3' }
      ] },
      { dim: '故障码读取', cells: [
        { text: '驱动器面板 + 调试软件', sid: 's_m1' },
        { text: '控制器状态字 + 诊断日志', sid: 's_m4' }
      ] },
      { dim: '过热类报警', cells: [
        { text: 'E-07 过热保护，检查风扇与风道', sid: 's_m4b' },
        { text: '按温度阈值停机检查，关注电源模块', sid: 's_m4b' }
      ] },
      { dim: '典型调试工具', cells: [
        { text: 'CDV 调试软件', sid: 's_m1' },
        { text: 'SPiiPlus MMI', sid: 's_m3' }
      ] },
      { dim: '张力控制', cells: [
        { text: '内置锥度 / 卷径补偿参数', sid: 's_m1' },
        { text: '需控制器程序实现，依赖 PDO 映射', sid: 's_m3' }
      ] }
    ]
  };

  const manualPages = {
    m1: {
      126: { no: 126, chapter: '第 7 章 · 张力控制', heading: '7.1 张力控制原理',
        body: ['CDV3000 通过速度环与张力环的协同实现恒张力收卷。张力环可采用模拟量张力传感器反馈或内部转矩估算两种方式。', '在高速收卷工况下，卷径的实时变化会直接改变张力力矩需求，因此需要启用卷径补偿。若卷径计算误差超过 3 mm，张力波动会明显增大。', '调试前应确认张力传感器量程与安装方向，并完成空载零点标定。'] },
      128: { no: 128, chapter: '第 7 章 · 张力控制', heading: '7.3 张力控制参数',
        body: ['张力控制参数组 P-04 包含锥度、卷径补偿与滤波设置。建议在低速段完成基础参数整定后再进入高速段验证。', 'P-04.12 张力锥度系数：设定收卷过程中的张力递减比例，范围 0–100%，默认 50%。参数受 P-04.10 卷径补偿模式影响。', 'P-04.13 张力滤波时间：默认 20 ms。提速段波动可通过适当增大滤波时间抑制，但过大会导致响应滞后。'],
        table: { head: ['参数', '名称', '范围', '出厂值'], rows: [
          ['P-04.10', '卷径补偿模式', '0–2', '2'],
          ['P-04.12', '张力锥度系数', '0–100%', '50%'],
          ['P-04.13', '张力滤波时间', '5–100 ms', '20 ms']
        ] }, highlight: 1 },
      130: { no: 130, chapter: '第 7 章 · 张力控制', heading: '7.4 卷径补偿',
        body: ['卷径补偿支持外部模拟量输入与内部计算两种模式。采用内部计算时需正确设置 P-04.04 初卷径与 P-04.05 材料厚度。', '内部计算模式通过线速度与电机转速推算实时卷径；若编码器分辨率设置错误，卷径计算会出现同比例偏差。'],
        highlight: 0 }
    },
    m2: {
      47: { no: 47, chapter: '第 5 章 · 通讯配置', heading: '5.2 EtherCAT PDO 配置',
        body: ['PDO 刷新周期建议与主站任务周期一致；干扰环境下可将 Sync 周期由 2 ms 调整至 1 ms，并检查屏蔽层接地。', '从站掉线时，优先检查物理层与 PDO 周期匹配情况，再排查主站负载。'],
        highlight: 0 }
    },
    m3: {
      89: { no: 89, chapter: '第 6 章 · 通讯', heading: '6.2 通讯参数配置',
        body: ['SPiiPlus 支持 EtherCAT 与 CANopen。PDO 映射偏移需在初始化阶段一次性配置；修改后必须重启通讯栈。', '轴参数与通讯配置应保存在同一配置文件中，避免控制器重启后映射错位。'],
        highlight: 0 }
    },
    m4: {
      91: { no: 91, chapter: '第 4 章 · 故障诊断', heading: '4.4 状态字与故障码',
        body: ['控制器状态字 bit 12 表示 Bus Error。该位在从站掉线或 PDO 周期异常时被置位。', 'Bus Error 对应状态字 bit 12。常见原因：从站掉线、PDO 周期不匹配、线缆屏蔽失效。建议采集诊断日志后再复位。', '复位前务必导出日志，否则首次故障现场将丢失。'],
        highlight: 1 },
      93: { no: 93, chapter: '第 4 章 · 故障诊断', heading: '4.6 E-07 故障处理流程',
        body: ['E-07 为驱动器过热保护。触发后驱动器会限制输出电流，需排除散热问题后方可复位。', '依次检查：电源模块温度、风扇转速、驱动器散热风道。若面板温度持续超过 75℃，应停机检查。', '更换风扇后建议空载运行 30 分钟，再带载运行 4 小时观察温度曲线。'],
        highlight: 1 }
    }
  };

  const cleanupRows = [
    { id: 'c1', file: '2025-03-12 华瑞-分切机跑偏.md', customer: '华瑞包装', project: '分切机跑偏处理', status: 'archived', skip: false, inferred: '客户来自 name；项目由文件名推断' },
    { id: 'c2', file: '星河纸业-复卷机异响处理.md', customer: '星河纸业', project: '复卷机异响处理', status: 'archived', skip: false, inferred: '项目由文件名推断' },
    { id: 'c3', file: '鑫源包装机切刀调整记录.md', customer: '', project: '包装机切刀调整记录', status: 'archived', skip: false, inferred: '客户未识别，请手动选择' },
    { id: 'c4', file: '蓝海收卷张力.md', customer: '蓝海新材料', project: '收卷张力调整', status: 'archived', skip: false, inferred: '全部可识别' },
    { id: 'c5', file: '恒信-灌装线编码器.md', customer: '恒信药业', project: '灌装线编码器更换', status: 'archived', skip: false, inferred: '项目由标题推断' },
    { id: 'c6', file: '2024-12 华瑞涂布机通讯.md', customer: '华瑞包装', project: '', status: 'archived', skip: false, inferred: '项目未识别，请手动填写' },
    { id: 'c7', file: 'Agito调试笔记-未整理.md', customer: '', project: '', status: 'archived', skip: false, inferred: '客户与项目均未识别' },
    { id: 'c8', file: '星河2号机ACS参数备份.md', customer: '星河纸业', project: '2号机 ACS 参数备份', status: 'archived', skip: false, inferred: '全部可识别' },
    { id: 'c9', file: '鑫源-枕式机切刀偏移-旧.md', customer: '鑫源食品', project: '枕式机切刀偏移', status: 'archived', skip: false, inferred: '客户来自 name' },
    { id: 'c10', file: '蓝海新材料现场照片记录.md', customer: '蓝海新材料', project: '现场照片记录', status: 'archived', skip: false, inferred: '项目由标题推断' },
    { id: 'c11', file: '售后通用排查清单.md', customer: '', project: '', status: 'archived', skip: false, inferred: '非项目笔记，建议跳过' },
    { id: 'c12', file: '恒信贴标机同步旧记录.md', customer: '恒信药业', project: '贴标机同步调整', status: 'archived', skip: false, inferred: '全部可识别' }
  ];

  /* vault 中「售后记录」以外的原有笔记；与售后笔记一起组成「所有笔记」 */
  const vaultExtras = [
    {
      path: '客户售后问题/售后通用排查清单.md', name: '售后通用排查清单.md', updated: '2026-11-08',
      body: `# 售后通用排查清单

## 上门前
- 确认设备型号、驱动器型号与固件版本
- 让客户提供故障发生时的工况与操作记录
- 提前核对相关手册章节，准备好调试软件

## 现场
1. 先看报警代码与状态字，不急于复位
2. 检查供电、接地、屏蔽与线缆走向
3. 参数调整前先导出原始参数备份

## 收尾
- 记录处理过程与验证结果
- 明确待跟进项与回访时间`
    },
    {
      path: '工作笔记/技术积累/伺服驱动器更换checklist.md', name: '伺服驱动器更换checklist.md', updated: '2026-11-20',
      body: `# 伺服驱动器更换 Checklist

## 更换前
- 记录原驱动器型号、固件版本与完整参数
- 确认电机铭牌、编码器类型与分辨率
- 拍照记录接线与端子位置

## 更换后
- 上电前核对母线电压与接线
- 导入参数后逐项核对编码器分辨率、电机极对数
- 先空载点动，再带载低速运行，最后按工艺提速

> 经验：编码器分辨率参数不一致时，表现为低速轻微抖动、定位偏差。`
    },
    {
      path: '工作笔记/技术积累/张力控制经验总结.md', name: '张力控制经验总结.md', updated: '2026-11-14',
      body: `# 张力控制经验总结

## 影响张力的三个主因
1. 卷径计算误差：超过 3 mm 时高速段波动明显
2. 张力传感器零点漂移：换卷后瞬时下坠
3. 锥度系数与材料不匹配：收卷端面松紧不一

## 调试顺序
- 静态标定 → 低速段整定 → 中速验证 → 高速段复核
- 每次只改一个参数，记录前后波形

## 常用参数
- 锥度系数：根据材料厚度在 50%–70% 之间调整
- 滤波时间：20 ms 起步，波动大时适度增大`
    },
    {
      path: '工作笔记/技术积累/EtherCAT干扰排查经验.md', name: 'EtherCAT干扰排查经验.md', updated: '2026-10-30',
      body: `# EtherCAT 干扰排查经验

## 排查顺序
- 物理层：网线屏蔽、接地、拖链磨损
- 拓扑：从站顺序与分支长度
- 周期：PDO 刷新周期是否与主站任务周期一致

## 典型处理
- 动力柜内走线过长时，重新布线并加装磁环
- 干扰环境下将 Sync 周期由 2 ms 调整为 1 ms
- 更换交换机端口只作为临时验证手段

> 偶发掉线不要先怀疑控制器，优先查屏蔽与接地。`
    },
    {
      path: '工作笔记/技术积累/常见故障码速查.md', name: '常见故障码速查.md', updated: '2026-11-22',
      body: `# 常见故障码速查

## 高创 CDV3000
- E-07：驱动器过热保护，检查风扇与散热风道
- E-09：编码器信号异常，检查电缆与屏蔽

## ACS
- Bus Error：状态字 bit 12，检查从站与 PDO 周期
- Following Error：跟随误差超限，检查增益与负载

## Agito
- 编码器信号异常：优先检查电缆拖链与接地`
    },
    {
      path: '工作笔记/技术积累/手册笔记/CDV3000参数速记.md', name: 'CDV3000参数速记.md', updated: '2026-11-11',
      body: `# CDV3000 参数速记

## 张力相关
- P-04.10 卷径补偿模式
- P-04.12 张力锥度系数
- P-04.13 张力滤波时间

## 通讯相关
- PDO 刷新周期与主站任务周期一致
- 干扰环境可调整 Sync 周期`
    },
    {
      path: '工作笔记/客户管理/客户联系人清单.md', name: '客户联系人清单.md', updated: '2026-11-18',
      body: `# 客户联系人清单

## 星河纸业
- 王工 · 复卷机 / 分切机 · 夜班较多，白天联系

## 华瑞包装
- 李经理 · 分切机 / 涂布机 · 需提前预约

## 鑫源食品
- 赵工 · 枕式包装机 · 换规格频繁

## 恒信药业
- 周主管 · 灌装机 / 贴标机 · 对停机时间敏感

> 联系人信息仅保存在本地 vault，不外传。`
    },
    {
      path: '工作笔记/客户管理/2026年度拜访记录.md', name: '2026年度拜访记录.md', updated: '2026-11-23',
      body: `# 2026 年度拜访记录

## 11 月
- 11-24 星河纸业：张力高速段复核，波形正常
- 11-21 鑫源食品：切刀相位调整，三个规格验证通过
- 11-18 恒信药业：Agito 编码器更换后回访

## 10 月
- 10-30 鑫源食品：驱动器更换与参数导入
- 10-24 蓝海新材料：张力锥度设置结案

## 待安排
- 华瑞包装：等待来料检测报告后再上门`
    },
    {
      path: '学习笔记/运动控制/运动控制基础笔记.md', name: '运动控制基础笔记.md', updated: '2026-09-12',
      body: `# 运动控制基础笔记

## 三环结构
- 电流环：响应最快，决定力矩输出
- 速度环：抑制负载扰动
- 位置环：保证定位精度

## 前馈与滤波
- 速度前馈可减小跟随误差
- 滤波过大导致响应滞后，过小则放大噪音

## 实践要点
- 先整定电流环，再速度环，最后位置环`
    },
    {
      path: '学习笔记/运动控制/PID参数整定.md', name: 'PID参数整定.md', updated: '2026-10-08',
      body: `# PID 参数整定笔记

## 经验步骤
1. 先将 I、D 置零，逐步增大 P 至出现轻微振荡
2. 回调 P 至稳定，再加入 I 消除稳态误差
3. 最后加入 D 抑制超调，注意噪音放大

## 常见问题
- 积分饱和：加限幅或分离式积分
- 高频抖动：检查反馈分辨率与滤波`
    },
    {
      path: '学习笔记/PLC/西门子S7-1200入门.md', name: '西门子S7-1200入门.md', updated: '2026-08-20',
      body: `# 西门子 S7-1200 入门

## 硬件
- 本体集成以太网口，支持 PROFINET
- 信号模块按通道分组，注意公共端接法

## 编程
- OB1 主循环，OB100 启动
- 数据类型：BOOL / INT / REAL / TIME

## 调试
- 使用 TIA Portal 在线监控
- 强制表仅用于临时排查，完成后及时解除`
    },
    {
      path: '项目复盘/2025年度售后复盘.md', name: '2025年度售后复盘.md', updated: '2026-01-15',
      body: `# 2025 年度售后复盘

## 数据
- 完成项目 32 个，平均处理周期 12 天
- 复访率最高的三类问题：张力波动、通讯干扰、编码器故障

## 问题
- 现场记录不及时，部分项目缺少波形与照片
- 手册版本不统一，排查时反复确认参数含义

## 改进
- 统一售后记录模板，按日期续写
- 建立手册索引，按品牌归档`
    },
    {
      path: '项目复盘/星河纸业张力项目复盘.md', name: '星河纸业张力项目复盘.md', updated: '2026-10-31',
      body: `# 星河纸业张力项目复盘

## 背景
2# 复卷机提速至 1200 m/min 后张力波动 ±6%。

## 处理
- 重新标定张力传感器零点
- 修正卷径系数
- 更换张力编码器并完成动静标定

## 结果
连续运行 4 小时，张力稳定在 ±1.5%。

## 遗留
- 一周后回访，补充高速段波形截图
- 观察不同克重下的锥度适应性`
    },
    {
      path: '模板/售后记录模板.md', name: '售后记录模板.md', updated: '2026-06-02',
      body: `# 售后记录模板

## YYYY-MM-DD 问题标题
**现象**：
**排查**：
**处理**：
**结果**：
**待跟进**：

提示：一个项目一份主笔记，按日期追加小节。`
    },
    {
      path: '模板/周报模板.md', name: '周报模板.md', updated: '2026-05-11',
      body: `# 周报模板

## 本周完成
-

## 进行中
-

## 风险与待跟进
-

## 下周计划
-`
    },
    {
      path: '日记/2026-11工作日记.md', name: '2026-11工作日记.md', updated: '2026-11-24',
      body: `# 2026-11 工作日记

## 11-24
星河纸业张力复核，高速段波形平稳。回访记录已补进项目笔记。

## 11-21
鑫源食品切刀相位调整，三个规格验证通过。客户要求下次换规格前提前通知。

## 11-18
恒信药业编码器更换完成，建议检查拖链磨损。`
    }
  ];

  const config = {
    llm: {
      provider: '火山方舟 Agent Plan', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
      apiKey: 'ark-demo-7f3a9c21', model: 'doubao-seed-1.6-pro', temperature: 0.3
    },
    embedding: {
      mode: 'cloud', provider: '火山方舟', model: 'doubao-embedding-vision', dim: 2048
    },
    paths: { vault: 'D:\\Obsidian\\售后库', notes: '客户售后问题', pdf: 'D:\\手册', codeRoots: ['D:\\Code', 'D:\\Work\\PLC'] },
    whitelist: ['客户售后问题'],
    /* 「所有笔记」中的知识库来源选择：目录级 + 单文件级（含排除），一期默认仅售后目录 */
    indexSelection: { folders: ['客户售后问题'], files: [], excludedFiles: [], excludedFolders: [] },
    brands: ['高创', 'ACS', 'Agito'],
    followUpDays: 7,
    indexDelay: 8,
    backup: { enabled: true, location: 'D:\\AkaiRye\\backup', retention: 7, last: '2026-11-25 02:00' },
    shortcuts: { enabled: true }
  };

  const trend = {
    weeks: { labels: ['10-07', '10-14', '10-21', '10-28', '11-04', '11-11', '11-18', '11-25'], values: [2, 3, 2, 4, 3, 5, 2, 4] },
    months: { labels: ['6月', '7月', '8月', '9月', '10月', '11月'], values: [6, 8, 5, 9, 7, 11] }
  };

  /* PDF 根目录中「尚未入库」的本地文件；已入库手册由 manuals 动态生成 */
  const localPdfFiles = [
    { id: 'p1', name: '高创 CDV3000 快速调试卡.pdf', folder: '高创', path: 'D:\\手册\\高创\\CDV3000 快速调试卡.pdf',
      brand: '高创', pages: 36, size: '1.2 MB', modified: '2026-11-21' },
    { id: 'p2', name: '高创 CDV3000 选型与接线样本.pdf', folder: '高创', path: 'D:\\手册\\高创\\CDV3000 选型与接线样本.pdf',
      brand: '高创', pages: 120, size: '5.4 MB', modified: '2026-10-30' },
    { id: 'p3', name: 'ACS SPiiPlus 旧项目通讯配置备份.pdf', folder: 'ACS', path: 'D:\\手册\\ACS\\SPiiPlus 旧项目通讯配置备份.pdf',
      brand: 'ACS', pages: 88, size: '4.6 MB', modified: '2026-09-18' },
    { id: 'p4', name: 'Agito AG-2000 错误码速查.pdf', folder: 'Agito', path: 'D:\\手册\\Agito\\AG-2000 错误码速查.pdf',
      brand: 'Agito', pages: 22, size: '0.9 MB', modified: '2026-11-10' },
    { id: 'p5', name: '汇川 MD500 系列用户手册.pdf', folder: '其他', path: 'D:\\手册\\其他\\汇川 MD500 系列用户手册.pdf',
      brand: '未知', pages: 312, size: '16.8 MB', modified: '2024-06-02' }
  ];

  return {
    today: TODAY, notes, manuals, repos, sources, answers, fragments, sessions, compare,
    manualPages, cleanupRows, config, trend, localPdfFiles, vaultExtras,
    nav: [
      { id: 'home', label: '首页', icon: 'home', route: '#/home' },
      { id: 'notes', label: '笔记', icon: 'notes', route: '#/notes' },
      { id: 'kb', label: '知识库', icon: 'book', route: '#/kb', badge: 'kb' },
      { id: 'terminal', label: '终端', icon: 'terminal', route: '#/terminal' },
      { id: 'repos', label: '代码仓库', icon: 'git', route: '#/repos' },
      { id: 'settings', label: '设置', icon: 'settings', route: '#/settings' }
    ],
    terminalPresets: ['C:\\Users\\Administrator', 'D:\\Code\\akai-workbench', 'D:\\Code\\after-sales-tools', 'D:\\Work\\PLC\\plc-samples'],
    terminalInitial: [
      { id: 't1', cwd: 'C:\\Users\\Administrator', title: 'Users', lines: [
        { t: 'out', text: 'Windows PowerShell 5.1 · 本机工作台终端（原型模拟）' },
        { t: 'cmd', text: 'git --version' },
        { t: 'out', text: 'git version 2.47.0.windows.2' },
        { t: 'cmd', text: 'ls' },
        { t: 'out', text: 'Desktop   Documents   Downloads   obsidian-backup   scripts' },
      ], running: false },
      { id: 't2', cwd: 'D:\\Code\\after-sales-tools', title: 'after-sales-tools', lines: [
        { t: 'cmd', text: 'npm run dev' },
        { t: 'out', text: 'VITE v5.4.8  ready in 412 ms' },
        { t: 'out', text: '➜  Local:   http://localhost:5173/' },
        { t: 'sys', text: '后台会话已保持 · 刷新页面后可恢复' }
      ], running: true }
    ]
  };
})();

<template>
  <el-card v-if="loadError" shadow="never" class="page-card">
    <el-result icon="warning" title="设置加载失败" :sub-title="loadError">
      <template #extra>
        <el-button type="primary" @click="load">重试</el-button>
      </template>
    </el-result>
  </el-card>
  <el-card shadow="never" class="page-card" v-if="loaded">
    <template #header>
      <div class="toolbar" style="margin-bottom: 0">
        <b>系统设置</b>
        <el-tag v-if="dirty" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
        <el-button v-if="dirty" type="primary" size="small" style="margin-left: 8px" @click="save">保存设置</el-button>
        <span style="margin-left: auto" class="tip">修改需点击「保存设置」后生效</span>
      </div>
    </template>
    <el-form label-position="top" class="settings-form">
      <div class="settings-flow">
          <div class="section-card"><div class="section-title">功能开关</div>
          <el-form-item label="115 分享转存">
            <div class="switch-row">
              <el-switch v-model="f.enable_share_transfer" @change="onToggleShareTransfer" />
              <span class="tip">关闭后新消息中的分享链接不再转存，排队任务自动跳过</span>
            </div>
          </el-form-item>
          <el-form-item label="云下载（ED2K）">
            <div class="switch-row">
              <el-switch v-model="f.enable_offline_download" />
              <span class="tip">关闭后新消息中的电驴/磁力链接不再提交云下载，排队任务自动跳过</span>
            </div>
          </el-form-item>

          </div>

          <div class="section-card"><div class="section-title">Telegram</div>
          <el-form-item label="接入方式">
            <el-radio-group v-model="f.tg_mode">
              <el-radio value="polling">网页轮询</el-radio>
              <el-radio value="userbot">Userbot</el-radio>
              <el-radio value="both">两者</el-radio>
            </el-radio-group>
            <div class="tip" style="width: 100%">{{ tgModeTip }}<br />
              <span class="warn-text">⚠️ 接入方式的变更保存后需重启程序，才会开启/停止对应监听（重启容器：<code>docker restart autosave115</code>）</span>
            </div>
          </el-form-item>

          <el-form-item v-if="f.tg_mode !== 'userbot'" label="轮询间隔">
            <div class="range-row">
              <el-select v-model="pollPreset" style="width: 220px">
                <el-option v-for="p in POLL_PRESETS" :key="p.label"
                           :label="p.label" :value="p.label" />
              </el-select>
              <span class="tip range-tip">每轮轮询在区间内随机等待，降低被识别风险；仅作用于网页轮询频道</span>
            </div>
          </el-form-item>

          <el-form-item v-if="f.tg_mode !== 'polling'" label="Userbot">
            <div class="ub-inline">
              <el-tag :type="ub.connected ? 'success' : 'danger'" size="small">
                {{ ub.connected ? '已连接' : '未连接' }}
              </el-tag>
              <span class="tip">{{ ub.connected
                ? 'TG 账号监听运行中（实时接收私密频道消息）'
                : '尚未登录：请到「账号管理」完成 Userbot 登录，否则接入方式为 Userbot 的频道无法收到消息' }}</span>
            </div>
          </el-form-item>

          <el-form-item label="TG 代理">
            <div class="range-row input-btn-row" style="width: 100%">
              <el-input v-model="f.proxy" placeholder="socks5://127.0.0.1:1080 或 http://127.0.0.1:7890（仅 TG 流量走代理，留空直连）"
                        :title="f.proxy === SECRET_MASK ? '已配置代理（显示为掩码）：清空后输入新地址可更换' : ''" />
              <el-button :loading="proxyTesting" @click="testProxy">测试代理</el-button>
            </div>
            <div class="tip" style="width: 100%">
              国内部署必填：所有 TG 流量（频道抓取/补漏/按钮解析/通知/Bot 指令）都走此代理，
              115 直连不受影响。<br />
              <span class="warn-text">注意：代理变更对网页轮询/Bot 即时生效，但已连接的 Userbot
              需重启程序或在「账号管理」重新登录后才会改走新代理。</span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">转存控制</div>
          <el-form-item label="转存间隔">
            <div class="range-row">
              <el-select v-model="transferPreset" style="width: 220px">
                <el-option v-for="p in TRANSFER_PRESETS" :key="p.label"
                           :label="p.label" :value="p.label" />
              </el-select>
              <span class="tip range-tip">每笔转存在区间内随机等待；频繁触发风控请选「稳健」</span>
            </div>
          </el-form-item>
          <el-form-item label="每日转存上限">
            <div class="range-row">
              <el-input-number v-model="f.daily_limit" :min="1" :max="50" :step="1" :step-strictly="true" :precision="0" />
              <span class="tip range-tip">1~50；115 单账号每日转存的安全上限，不建议设为最大值</span>
            </div>
          </el-form-item>
          <el-form-item label="分享体积下限">
            <div class="range-row">
              <el-input-number v-model="f.min_size_gb" :min="0" :max="1000" :step="0.1" :precision="1" />
              <span class="tip range-tip">单位 GB；低于此值的分享视为广告并自动跳过，0 表示关闭</span>
            </div>
          </el-form-item>
          <el-form-item label="失败重试">
            <div class="range-row">
              <el-select v-model="retryPreset" style="width: 220px">
                <el-option v-for="p in RETRY_PRESETS" :key="p.label"
                           :label="p.label" :value="p.label" />
              </el-select>
              <span class="tip range-tip">失败后按预设间隔逐级重试（共 3 次）</span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">115防风控</div>
          <el-form-item label="定时计划" id="setting-schedule-item">
            <div class="switch-row">
              <el-switch v-model="f.schedule_enabled" />
              <span class="tip">开启后仅在允许时段内执行转存、云下载与自动文件整理（任务排队保留，时段外自动等待，规避深夜异常特征）</span>
            </div>
            <div class="range-row" style="margin-top: 8px" v-if="f.schedule_enabled">
              <el-time-select v-model="f.schedule_start" start="00:00" step="00:30" end="23:30"
                              placeholder="开始" style="width: 110px" />
              <span class="sep">~</span>
              <el-time-select v-model="f.schedule_end" start="00:00" step="00:30" end="23:30"
                              placeholder="结束" style="width: 110px" />
              <span class="tip">支持跨零点（如 22:00 ~ 08:00）</span>
            </div>
          </el-form-item>

          <el-form-item label="开工随机扰动">
            <div class="range-row">
              <el-input-number v-model="f.schedule_jitter_min" :min="0" :max="30" :step="1" :step-strictly="true" :precision="0" :disabled="!f.schedule_enabled" />
              <span class="tip range-tip">
                单位：分钟；定时时段到达后随机延迟 0~N 分钟开工，消除整点脚本特征（0 表示不扰动）
                <template v-if="!f.schedule_enabled">
                  <span class="warn-text">（需先开启上方「定时计划」开关）</span>
                  <el-button link type="primary" size="small" style="margin-left: 6px; font-weight: 500" @click="enableSchedule">
                    一键开启
                  </el-button>
                </template>
              </span>
            </div>
          </el-form-item>

          <el-form-item label="拟人化连存小憩">
            <div class="switch-row">
              <el-switch v-model="f.batch_rest_enabled" />
              <span class="tip">连续完成多笔转存后自动插入小憩，模拟真人离开电脑、喝水或浏览，打破固定匀速特征</span>
            </div>
            <template v-if="f.batch_rest_enabled">
              <div class="range-row" style="margin-top: 8px">
                <span class="tip">每连续完成</span>
                <el-input-number v-model="f.batch_rest_count" :min="2" :max="20" :step="1" :step-strictly="true" :precision="0" style="width: 126px" />
                <span class="tip">笔转存，</span>
              </div>
              <div class="range-row" style="margin-top: 8px">
                <span class="tip">随机小憩</span>
                <el-input-number v-model="f.batch_rest_min_min" :min="1" :max="60" :step="1" :step-strictly="true" :precision="0" style="width: 126px" />
                <span class="sep">~</span>
                <el-input-number v-model="f.batch_rest_max_min" :min="1" :max="60" :step="1" :step-strictly="true" :precision="0" style="width: 126px" />
                <span class="tip">分钟（区间：最短不能大于最长）</span>
              </div>
            </template>
          </el-form-item>

          <el-form-item label="后台巡检周期">
            <div class="range-row">
              <el-select v-model="f.health_check_interval_min" style="width: 220px">
                <el-option label="每 5 分钟" :value="5" />
                <el-option label="每 10 分钟（推荐）" :value="10" />
                <el-option label="每 15 分钟" :value="15" />
                <el-option label="每 30 分钟" :value="30" />
              </el-select>
              <span class="tip range-tip">空闲时探测 115 Cookie 存活状态的间隔；适度放宽可削减 80% 的无效背景心跳调用</span>
            </div>
          </el-form-item>

          <el-form-item label="查重目录缓存">
            <div class="range-row">
              <el-select v-model="f.dedup_cache_ttl_sec" style="width: 220px">
                <el-option label="不缓存（每次穿透查）" :value="0" />
                <el-option label="30 秒" :value="30" />
                <el-option label="60 秒" :value="60" />
                <el-option label="90 秒（推荐）" :value="90" />
                <el-option label="180 秒" :value="180" />
              </el-select>
              <span class="tip range-tip">同目录多集转存时复用最近读取的目录列表，避免每存一集就重复请求 115 目录接口</span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">TG 通知</div>
          <el-form-item label="转存结果通知">
            <div class="switch-row">
              <el-switch v-model="f.notify_transfer_success" />
              <span class="tip">转存成功/失败、云下载提交/完成/失败都会推送 TG 消息（含具体原因）</span>
            </div>
          </el-form-item>
          <el-form-item label="TG 推送">
            <SecretConfig :configured="!!(f.notify_token && f.notify_chat_id)" :save="saveNotify"
                          @open="openNotify" @cancel="resetNotify">
              <div class="range-row" style="width: 100%">
                <el-input v-model="nf.token" type="password" show-password
                          placeholder="TG Bot Token（@BotFather 获取）" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-input v-model="nf.chat_id" placeholder="Chat ID（你的用户 ID）" style="width: 260px" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button type="primary" plain size="small" :loading="testing"
                           @click="testNotify(nf.token, nf.chat_id)">发送测试消息</el-button>
              </div>
              <div class="tip" style="width: 100%">
                填写后无需保存即可直接测试；测试失败会提示具体原因。收不到时检查：① Token 是否完整复制
                ② 是否已给 Bot 发送 /start ③ 国内服务器需在上方「TG 代理」处填写代理地址
              </div>
            </SecretConfig>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">转存增强</div>
          <el-form-item label="分享追更">
            <div class="switch-row">
              <el-switch v-model="f.share_update_enabled" :disabled="!f.enable_share_transfer" />
              <span class="tip">
                同一分享链接再次出现时不再判重：自动比对目标目录，只转存新增的集数文件，已存过的不重复（适配分享方往同一链接持续加更的追剧场景）
                <span v-if="!f.enable_share_transfer" class="warn-text">（需先开启上方「115 分享转存」总开关）</span>
              </span>
            </div>
          </el-form-item>
          <el-form-item label="按文件名去重">
            <div class="switch-row">
              <el-switch v-model="f.dedup_by_name" :disabled="!f.enable_share_transfer && !f.enable_offline_download" />
              <span class="tip">开启后转存前再按文件名查重：网盘同名目录下已存在同名文件时自动跳过</span>
            </div>
          </el-form-item>
          <el-form-item label="整理刮削">
            <div class="switch-row">
              <el-button type="primary" plain size="small" @click="$router.push('/media')">
                前往独立「整理刮削」工作台 →
              </el-button>
              <span class="tip">
                支持电影/剧集标准命名、[tmdbid] 标签、TMDB 精准刮削、本地 NFO/海报与 TG 联动人工确认
              </span>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">通知渠道扩展</div>
          <el-form-item label="Bark（iOS）">
            <SecretConfig :configured="!!f.bark_url" :save="saveBark"
                          @open="openBark" @cancel="resetBark">
              <div class="range-row" style="width: 100%">
                <el-input v-model="bf.url" type="password" show-password
                          placeholder="https://api.day.app/你的key" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button size="small" :loading="chTesting === 'bark'" @click="testChannel('bark')">发送测试</el-button>
              </div>
              <div class="tip" style="width: 100%">
                iOS Bark 推送：App Store 安装 Bark 后，复制 App 内显示的完整推送 URL
                （形如 <code>https://api.day.app/你的key</code>，自建服务器换域名即可）
              </div>
            </SecretConfig>
          </el-form-item>
          <el-form-item label="PushPlus">
            <SecretConfig :configured="!!f.pushplus_token" :save="savePushPlus"
                          @open="openPushPlus" @cancel="resetPushPlus">
              <div class="range-row" style="width: 100%">
                <el-input v-model="pf.token" type="password" show-password
                          placeholder="pushplus.plus 的 Token" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-input v-model="pf.topic" placeholder="群组编码（可选，留空发本人）" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button size="small" :loading="chTesting === 'pushplus'" @click="testChannel('pushplus')">发送测试</el-button>
              </div>
              <div class="tip" style="width: 100%">
                微信推送：官网 pushplus.plus 微信扫码登录后复制 Token，默认发给本人微信；
                需一对多群发时填「一对一推送 → 群组管理」里创建的群组编码
              </div>
            </SecretConfig>
          </el-form-item>
          <el-form-item label="ntfy">
            <SecretConfig :configured="!!f.ntfy_url" :save="saveNtfy"
                          @open="openNtfy" @cancel="resetNtfy">
              <div class="range-row" style="width: 100%">
                <el-input v-model="tf.url" placeholder="https://ntfy.sh/你的主题" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-input v-model="tf.token" type="password" show-password
                          placeholder="访问令牌（可选，自建 ntfy 开启鉴权时填）" />
              </div>
              <div class="range-row" style="width: 100%">
                <el-button size="small" :loading="chTesting === 'ntfy'" @click="testChannel('ntfy')">发送测试</el-button>
              </div>
              <div class="tip" style="width: 100%">
                完整的 ntfy 主题 URL，如 <code>https://ntfy.sh/my-115-topic</code>（自建服务器换域名）；
                手机装 ntfy App 订阅同一主题即可收推送。主题名建议加随机后缀防止被他人订阅；
                使用官方 ntfy.sh 时访问令牌留空即可
              </div>
            </SecretConfig>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">每日摘要</div>
          <el-form-item label="每日摘要">
            <div class="switch-row">
              <el-switch v-model="f.digest_enabled" />
              <el-tooltip :disabled="!!f.digest_enabled" content="请先开启「每日摘要」总开关" placement="top">
                <span>
                  <el-button :loading="digestTesting" :disabled="!f.digest_enabled" @click="testDigest">立即推送一次</el-button>
                </span>
              </el-tooltip>
            </div>
            <div class="tip" style="width: 100%">
              开启后每天定时把当日转存汇总推送到已配置的通知渠道（TG / Bark / PushPlus / ntfy）；
              「立即推送一次」用与定时推送完全相同的内容验证渠道是否可达
            </div>
          </el-form-item>
          <el-form-item v-if="f.digest_enabled" label="推送时间">
            <el-time-select v-model="f.digest_time" start="00:00" step="00:30" end="23:30"
                            placeholder="选择时间" style="width: 130px" />
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">定时自动备份</div>
          <el-form-item label="自动备份">
            <div class="switch-row">
              <el-switch v-model="f.auto_backup_enabled" />
              <span class="tip">开启后定时把核心设置/业务规则/转存与整理台账打包推送到 TG Bot（超 8MB 自动 Gzip 压缩），可直接发回 Bot 或在本页恢复</span>
            </div>
          </el-form-item>
          <el-form-item label="备份间隔">
            <div class="range-row">
              <el-input-number v-model="f.auto_backup_days" :min="1" :max="365" :step="1" :step-strictly="true" :precision="0" :disabled="!f.auto_backup_enabled" />
              <span class="tip">天（1~365 天）</span>
              <template v-if="f.auto_backup_enabled">
                <span class="tip">推送时间：</span>
                <el-time-select v-model="f.auto_backup_time" start="00:00" step="00:30" end="23:30"
                                placeholder="选择时间" style="width: 120px" />
                <span class="tip range-tip">周期到达当天在此时间推送（默认 04:00，推荐凌晨闲时备份）</span>
              </template>
              <span v-else class="warn-text">（需先开启上方「自动备份」总开关）</span>
            </div>
          </el-form-item>
          <el-form-item label="备份数据">
            <div class="range-row" style="width: 100%">
              <el-button @click="backupExport">导出数据备份</el-button>
            </div>
            <div class="tip" style="width: 100%">
              备份含设置/频道/规则/路由/黑名单/订阅/转存记录/自动整理/整理刮削/操作账本及待刮削/跳过任务（JSON，不含登录密码、Bot Token 等敏感凭据与本地海报目录）
            </div>
          </el-form-item>
          <el-form-item label="从备份恢复">
            <div class="range-row" style="width: 100%">
              <input type="file" ref="restoreInput" accept=".json,.gz,.zip,application/json,application/gzip,application/x-gzip,application/zip"
                     style="display: none" @change="onRestoreFile" />
              <el-button @click="$refs.restoreInput.click()">选择备份文件（JSON / GZ）</el-button>
              <span v-if="restoreFile" class="selected-count">
                {{ restoreFile.name }}（{{ Math.max(1, Math.round(restoreFile.size / 1024)) }} KB）
              </span>
              <el-button type="danger" plain :loading="restoring" :disabled="!restoreFile"
                         @click="doRestore">恢复数据</el-button>
            </div>
            <div class="tip" style="width: 100%">
              支持网页导出的 <code>.json</code> 与 TG 自动备份的 <code>.json.gz</code>（及 <code>.zip</code>），将<b class="warn-text">整体覆盖</b>上述全部业务数据
              （登录密码与通知密钥保留不变；新版备份可恢复待刮削/跳过任务，旧版备份会清空旧刮削队列，恢复后系统回到停止状态）。也可把备份文件私聊发给 Bot，回复 <code>/restore confirm</code> 恢复
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">整机迁移包（ZIP · 跨机完整迁移）</div>
          <el-form-item label="体积预估">
            <div class="bundle-est-box">
              <div class="range-row" style="justify-content: space-between">
                <span v-if="bundleEst" class="bundle-est-metrics">
                  数据库 <b>{{ fmtSize(bundleEst.db_bytes) }}</b> ·
                  待刮削 <b>{{ bundleEst.pending_media_tasks }}</b>/{{ bundleEst.total_media_tasks }} 条 ·
                  NFO <b>{{ bundleEst.meta_nfo_count }}</b> 个（{{ fmtSize(bundleEst.meta_nfo_bytes) }}）·
                  海报图 <b>{{ bundleEst.meta_asset_count }}</b> 个（{{ fmtSize(bundleEst.meta_asset_bytes) }}）·
                  凭据 <b>{{ bundleEst.credentials_count }}</b> 项
                </span>
                <span v-else class="tip">点击刷新获取当前数据与本地海报体积估算</span>
                <el-button size="small" :loading="estLoading" @click="loadBundleEstimate">刷新估算</el-button>
              </div>
              <div v-if="bundleEst?.meta_root" class="tip" style="margin-top: 4px">
                当前本地元数据目录：<code>{{ bundleEst.meta_root }}</code>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="导出迁移包">
            <div class="bundle-opts">
              <div class="range-row">
                <span class="bundle-label">本地元数据：</span>
                <el-radio-group v-model="bundleOpt.meta_mode" class="bundle-capsule-radios">
                  <el-radio-button value="nfo">仅 .nfo（推荐）</el-radio-button>
                  <el-radio-button value="all">完整（含海报/背景图）</el-radio-button>
                  <el-radio-button value="none">不含元数据</el-radio-button>
                </el-radio-group>
              </div>
              <div class="bundle-switch-group">
                <label class="bundle-check">
                  <el-switch v-model="bundleOpt.slim_db" />
                  <span>瘦身数据库副本（剥离日志/TMDB 缓存并压缩，不影响当前运行库）</span>
                </label>
                <label class="bundle-check">
                  <el-switch v-model="bundleOpt.include_credentials" />
                  <span>包含凭据与 Session（115 Cookie / TG Session / 密钥 / 登录密码，导出需验证密码）</span>
                </label>
              </div>
              <div class="range-row" style="margin-top: 2px">
                <el-button type="primary" :loading="bundleExporting" @click="exportBundle">导出迁移包（.zip）</el-button>
                <span v-if="bundleEst" class="tip">
                  当前所选内容估算原始体积约 <b>{{ fmtSize(estBundleBytes) }}</b>（ZIP 下载时流式生成，不占额外内存）
                </span>
              </div>
              <div v-if="bundleOpt.include_credentials" class="tip warn-text">
                安全提示：含凭据的迁移包等同于完整账号与登录权限，请勿上传至公网网盘或分享给他人。
              </div>
            </div>
          </el-form-item>
          <el-form-item label="从迁移包恢复">
            <div class="bundle-opts">
              <div class="range-row">
                <input type="file" ref="bundleInput" accept=".zip,application/zip"
                       style="display: none" @change="onBundleFile" />
                <el-button @click="$refs.bundleInput.click()">选择迁移包（.zip）</el-button>
                <span v-if="bundleFile" class="selected-count">
                  {{ bundleFile.name }}（{{ fmtSize(bundleFile.size) }}）
                </span>
                <el-button type="danger" plain :loading="bundleRestoring" :disabled="!bundleFile"
                           @click="doBundleRestore">预览并恢复迁移包</el-button>
              </div>
              <div class="bundle-switch-group">
                <label class="bundle-check">
                  <el-switch v-model="bundleRestoreOpt.restore_credentials" />
                  <span>覆盖恢复凭据与 Session（仅在压缩包内含凭据时生效；关闭则保留本机密码与登录态）</span>
                </label>
                <label class="bundle-check">
                  <el-switch v-model="bundleRestoreOpt.restore_meta" />
                  <span>还原本地元数据（.nfo 与海报图片，并自动迁移路径到本机元信息目录）</span>
                </label>
              </div>
              <div v-if="bundleRestoring" class="bundle-progress-wrap">
                <div class="tip">{{ bundlePhase }}</div>
                <el-progress :percentage="bundleProgress" :stroke-width="14" />
              </div>
              <div class="tip">
                支持最大 <b>2 GB</b> 整机迁移包流式上传（导入前需先在顶部停机）；恢复后本机任务回到停止状态，若还原了密钥将自动刷新当前登录令牌。
              </div>
            </div>
          </el-form-item>
          </div>

          <div class="section-card"><div class="section-title">对外 API</div>
          <el-form-item label="API Token">
            <div class="range-row input-btn-row" style="width: 100%">
              <el-input v-model="f.api_token" type="password" show-password
                        :placeholder="f.api_token === SECRET_MASK ? '已配置（保持不变即继续生效；清空并保存则关闭对外 API）' : '未设置时对外 API 关闭'" clearable />
              <el-button @click="genToken">生成随机 Token</el-button>
            </div>
            <div class="tip" style="width: 100%">
              留空关闭。开启后调用方携带请求头 <code>X-API-Token</code> 访问 <code>/api/ext/v1/*</code> 接口
            </div>
          </el-form-item>
          </div>
      </div>

      <div class="btn-row" style="margin-top: 10px">
        <el-button type="primary" :disabled="!dirty" @click="save">保存设置</el-button>
      </div>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg, fmtSize, saveOk, SECRET_MASK } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'

const f = ref({})
const loaded = ref(false)
const loadError = ref('')
// 失败重试只提供预设档位（不开放自定义秒数，避免误配过密触发风控）
const RETRY_PRESETS = [
  { label: '快速（30秒/2分/10分）', sec: [30, 120, 600] },
  { label: '标准（1分/5分/30分）', sec: [60, 300, 1800] },
  { label: '稳健（5分/30分/2小时）', sec: [300, 1800, 7200] },
]
const retryPreset = ref('标准（1分/5分/30分）')
// 轮询/转存间隔同样只提供档位：区间随档位固定，防止自定义过密触发风控
const POLL_PRESETS = [
  { label: '快速（15~45秒）', min: 15, max: 45 },
  { label: '标准（30秒~2分）', min: 30, max: 120 },
  { label: '稳健（3~10分）', min: 180, max: 600 },
]
const pollPreset = ref('标准（30秒~2分）')
const TRANSFER_PRESETS = [
  { label: '快速（30秒~1分）', min: 30, max: 60 },
  { label: '标准（1~3分）', min: 60, max: 180 },
  { label: '稳健（3~10分）', min: 180, max: 600 },
]
const transferPreset = ref('标准（1~3分）')
const testing = ref(false)
const chTesting = ref('') // 正在测试的通知渠道（bark/pushplus/ntfy）
const dirty = ref(false)

const enableSchedule = () => {
  f.value.schedule_enabled = true
  dirty.value = true
  nextTick(() => {
    const el = document.getElementById('setting-schedule-item')
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('highlight-pulse')
      setTimeout(() => el.classList.remove('highlight-pulse'), 1600)
    }
  })
}

// ---- 密钥类配置的独立编辑/保存（参考 TMDB API Key 的「已配置 + 更换」模式）----
// 每块用独立 draft，编辑时不联动底部表单，保存成功后同步回 f（避免底部保存把旧值覆盖回去）
import SecretConfig from '../components/SecretConfig.vue'
const nf = reactive({ token: '', chat_id: '' })   // TG 通知
const bf = reactive({ url: '' })                  // Bark
const pf = reactive({ token: '', topic: '' })     // PushPlus
const tf = reactive({ url: '', token: '' })       // ntfy

const syncF = (patch) => {
  suppressWatch = true
  Object.assign(f.value, patch)
  nextTick(() => { suppressWatch = false })
}

const openNotify = () => { nf.token = ''; nf.chat_id = f.value.notify_chat_id || '' }
const resetNotify = () => { nf.token = ''; nf.chat_id = f.value.notify_chat_id || '' }
const saveNotify = async () => {
  if (!nf.token.trim() || !nf.chat_id.trim()) { ElMessage.error('请完整填写 Bot Token 和 Chat ID'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { notify_token: nf.token.trim(), notify_chat_id: nf.chat_id.trim() })
    syncF({ notify_token: SECRET_MASK, notify_chat_id: nf.chat_id.trim() })
    saveOk('TG 通知配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

const openBark = () => { bf.url = '' }
const resetBark = () => { bf.url = '' }
const saveBark = async () => {
  if (!bf.url.trim()) { ElMessage.error('请填写 Bark 推送 URL'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { bark_url: bf.url.trim() })
    syncF({ bark_url: SECRET_MASK })
    saveOk('Bark 配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

const openPushPlus = () => { pf.token = ''; pf.topic = f.value.pushplus_topic || '' }
const resetPushPlus = () => { pf.token = ''; pf.topic = f.value.pushplus_topic || '' }
const savePushPlus = async () => {
  if (!pf.token.trim()) { ElMessage.error('请填写 PushPlus Token'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { pushplus_token: pf.token.trim(), pushplus_topic: pf.topic.trim() })
    syncF({ pushplus_token: SECRET_MASK, pushplus_topic: pf.topic.trim() })
    saveOk('PushPlus 配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

const openNtfy = () => { tf.url = ''; tf.token = '' }
const resetNtfy = () => { tf.url = ''; tf.token = '' }
const saveNtfy = async () => {
  if (!tf.url.trim()) { ElMessage.error('请填写 ntfy 主题 URL'); throw new Error('invalid') }
  try {
    await api.put('/api/settings', { ntfy_url: tf.url.trim(), ntfy_token: tf.token.trim() })
    syncF({ ntfy_url: tf.url.trim(), ntfy_token: SECRET_MASK })
    saveOk('ntfy 配置已保存', '已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)); throw e }
}

// ---- 从备份恢复（网页上传）----
const restoreInput = ref(null)
const restoreFile = ref(null)
const restoring = ref(false)
const onRestoreFile = (ev) => {
  const file = ev.target.files?.[0]
  restoreFile.value = null
  if (!file) return
  if (file.size > 20 * 1024 * 1024) { ElMessage.error('文件超过 20MB 上限，无法作为备份导入'); return }
  restoreFile.value = file
  ev.target.value = '' // 允许重复选择同一文件
}
const fmtBackupCounts = (s) => (
  `频道 ${s.channels || 0}、规则 ${s.rules || 0}、路由 ${s.routes || 0}、` +
  `黑名单 ${s.blacklist || 0}、订阅 ${s.subscriptions || 0}、转存 ${s.transfers || 0}、` +
  `自动整理 ${s.auto_move_rules || 0}、整理刮削 ${s.media_rules || 0}、操作账本 ${s.organized_records || 0}` +
  (s.media_tasks != null ? `、待刮削任务 ${s.media_tasks}` : '')
)

const extractBlobErr = async (e) => {
  const d = e?.response?.data
  if (d instanceof Blob) {
    try {
      const txt = await d.text()
      const parsed = JSON.parse(txt)
      if (parsed?.detail) return String(parsed.detail)
    } catch { /* ignore */ }
  }
  return errMsg(e)
}

const doRestore = async () => {
  const file = restoreFile.value
  if (!file) return
  restoring.value = true
  try {
    const head = new Uint8Array(await file.slice(0, 2).arrayBuffer())
    const lowerName = (file.name || '').toLowerCase()
    const isCompressed = (
      lowerName.endsWith('.gz') || lowerName.endsWith('.json.gz') || lowerName.endsWith('.zip')
      || (head[0] === 0x1f && head[1] === 0x8b)
      || (head[0] === 0x50 && head[1] === 0x4b)
    )
    const kb = Math.max(1, Math.round(file.size / 1024))
    let summary
    let rawBuf = null
    let jsonData = null
    if (isCompressed) {
      rawBuf = await file.arrayBuffer()
      const { data: prev } = await api.post('/api/backup/preview', rawBuf, {
        timeout: 60000,
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Backup-Filename': encodeURIComponent(file.name || 'backup.json.gz'),
        },
      })
      summary = prev.summary
    } else {
      const text = await file.text()
      try { jsonData = JSON.parse(text) } catch { throw new Error('文件不是合法 JSON') }
      if (!jsonData?._meta || !jsonData.settings) throw new Error('不是本程序的备份文件（缺少备份标记）')
      summary = {
        exported_at: jsonData._meta.exported_at || '未知时间',
        settings: Object.keys(jsonData.settings || {}).length,
        channels: (jsonData.channels || []).length,
        rules: (jsonData.rules || []).length,
        routes: (jsonData.routes || []).length,
        blacklist: (jsonData.blacklist || []).length,
        subscriptions: (jsonData.subscriptions || []).length,
        transfers: (jsonData.transfers || []).length,
        auto_move_rules: (jsonData.auto_move_rules || []).length,
        media_rules: (jsonData.media_rules || []).length,
        organized_records: (jsonData.organized_records || []).length,
        media_tasks: Array.isArray(jsonData.media_tasks) ? jsonData.media_tasks.length : 0,
      }
    }
    await ElMessageBox.confirm(
      `备份导出于 ${summary.exported_at || '未知时间'}（${kb} KB），包含：` +
      `设置 ${summary.settings || 0} 项、${fmtBackupCounts(summary)}。` +
      `恢复将覆盖当前全部业务数据（登录密码与通知密钥不变；新版备份含待刮削任务，旧版备份会清空现有队列），且系统回到停止状态。确定继续？`,
      '确认恢复备份', { confirmButtonText: '确认恢复', cancelButtonText: '取消', type: 'warning' })
    const { data: resp } = isCompressed
      ? await api.post('/api/backup/restore', rawBuf, {
          timeout: 120000,
          headers: {
            'Content-Type': 'application/octet-stream',
            'X-Backup-Filename': encodeURIComponent(file.name || 'backup.json.gz'),
          },
        })
      : await api.post('/api/backup/restore', jsonData, { timeout: 120000 })
    await ElMessageBox.alert(
      `恢复完成：设置 ${resp.stats.settings} 项、${fmtBackupCounts(resp.stats)}。` +
      `页面即将刷新以加载恢复后的数据。`, '恢复成功', { confirmButtonText: '刷新页面' })
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(errMsg(e))
  } finally {
    restoring.value = false
  }
}

// ---- 整机迁移包（ZIP 流式导出 / 预览 / 导入）----
const bundleEst = ref(null)
const estLoading = ref(false)
const bundleExporting = ref(false)
const bundleOpt = reactive({
  meta_mode: 'nfo',
  slim_db: true,
  include_credentials: false,
})
const bundleInput = ref(null)
const bundleFile = ref(null)
const bundleRestoring = ref(false)
const bundlePhase = ref('')
const bundleProgress = ref(0)
const bundleRestoreOpt = reactive({
  restore_credentials: false,
  restore_meta: true,
})
const MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024

const loadBundleEstimate = async () => {
  estLoading.value = true
  try {
    const { data } = await api.get('/api/backup/bundle/estimate')
    bundleEst.value = data
  } catch { /* ignore */ } finally {
    estLoading.value = false
  }
}

const estBundleBytes = computed(() => {
  const e = bundleEst.value
  if (!e) return 0
  let total = e.db_bytes || 0
  if (bundleOpt.include_credentials) total += e.credentials_bytes || 0
  if (bundleOpt.meta_mode === 'nfo') total += e.meta_nfo_bytes || 0
  else if (bundleOpt.meta_mode === 'all') total += (e.meta_nfo_bytes || 0) + (e.meta_asset_bytes || 0)
  return total
})

const exportBundle = async () => {
  let password = ''
  try {
    if (bundleOpt.include_credentials) {
      const res = await ElMessageBox.prompt(
        '迁移包将包含 115 Cookie、TG Session、加密密钥与 Web 登录密码哈希。请输入当前登录密码确认导出：',
        '安全验证',
        {
          inputType: 'password',
          confirmButtonText: '验证并导出',
          cancelButtonText: '取消',
          inputPlaceholder: '请输入当前 Web 登录密码',
        },
      )
      password = (res?.value || '').trim()
      if (!password) {
        ElMessage.warning('请输入当前登录密码后再导出含凭据迁移包')
        return
      }
    }
    bundleExporting.value = true
    const resp = await api.post('/api/backup/bundle/export', {
      include_credentials: bundleOpt.include_credentials,
      meta_mode: bundleOpt.meta_mode,
      slim_db: bundleOpt.slim_db,
      password,
    }, { responseType: 'blob', timeout: 600000 })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    const cd = resp.headers['content-disposition'] || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    a.download = m ? m[1] : 'autosave_migration.zip'
    a.click()
    URL.revokeObjectURL(url)
    saveOk(
      '整机迁移包已导出',
      bundleOpt.include_credentials ? '已包含凭据与 Session，请妥善加密保管' : '已打包数据库与所选元数据（不含敏感凭据）',
    )
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(await extractBlobErr(e))
  } finally {
    bundleExporting.value = false
  }
}

const onBundleFile = (ev) => {
  const file = ev.target.files?.[0] || null
  ev.target.value = ''
  if (!file) return
  if (file.size > MAX_BUNDLE_BYTES) {
    ElMessage.error(`迁移包体积超过 2 GB 上限（当前 ${fmtSize(file.size)}），请导出「仅 .nfo」版本后再迁移`)
    return
  }
  if (!(file.name || '').toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请选择 .zip 格式的整机迁移包')
    return
  }
  bundleFile.value = file
  bundleProgress.value = 0
  bundlePhase.value = ''
}

const doBundleRestore = async () => {
  const file = bundleFile.value
  if (!file) return
  bundleRestoring.value = true
  bundleProgress.value = 0
  bundlePhase.value = '正在上传并校验迁移包（1/2）'
  try {
    const { data: prevResp } = await api.post('/api/backup/bundle/preview', file, {
      timeout: 600000,
      headers: { 'Content-Type': 'application/octet-stream' },
      onUploadProgress: (ev) => {
        if (ev.total) bundleProgress.value = Math.min(99, Math.round((ev.loaded / ev.total) * 100))
      },
    })
    bundleProgress.value = 100
    const p = prevResp.preview || {}
    const c = p.counts || {}
    const credLine = p.has_credentials
      ? (bundleRestoreOpt.restore_credentials
        ? `凭据与 Session：包内包含（${(p.credential_files || []).join('、')}），将【覆盖本机凭据与密码】`
        : '凭据与 Session：包内包含，当前设为【保留本机凭据（跳过覆盖）】')
      : '凭据与 Session：包内未包含，将保留本机密码与登录态'
    const metaLine = p.has_meta
      ? (bundleRestoreOpt.restore_meta
        ? `本地元数据：NFO ${p.meta_nfo_count || 0} 个、图片 ${p.meta_asset_count || 0} 个（${fmtSize(p.meta_bytes || 0)}），将【恢复并适配本机目录】`
        : `本地元数据：包内含 ${p.meta_nfo_count || 0} NFO / ${p.meta_asset_count || 0} 图片，当前设为【跳过不恢复】`)
      : '本地元数据：包内未包含'
    await ElMessageBox.confirm(
      `迁移包导出于 ${p.exported_at || '未知时间'}（压缩包 ${fmtSize(p.bundle_bytes || file.size)}）。\n` +
      `数据库内容：设置 ${c.settings || 0} 项、${fmtBackupCounts(c)}。\n` +
      `${credLine}。\n${metaLine}。\n` +
      `恢复将整库替换当前数据且系统回到停止状态，确定继续？`,
      '确认从整机迁移包恢复',
      { confirmButtonText: '确认覆盖恢复', cancelButtonText: '取消', type: 'warning' },
    )

    bundleProgress.value = 0
    bundlePhase.value = '正在上传并写入整机数据（2/2）'
    const qs = `restore_credentials=${bundleRestoreOpt.restore_credentials ? 'true' : 'false'}&restore_meta=${bundleRestoreOpt.restore_meta ? 'true' : 'false'}`
    const { data: res } = await api.post(`/api/backup/bundle/restore?${qs}`, file, {
      timeout: 600000,
      headers: { 'Content-Type': 'application/octet-stream' },
      onUploadProgress: (ev) => {
        if (ev.total) bundleProgress.value = Math.min(99, Math.round((ev.loaded / ev.total) * 100))
      },
    })
    bundleProgress.value = 100
    // 恢复凭据后服务端已换发新会话并经 Set-Cookie 下发，前端不再保存 token
    const rc = res.counts || {}
    await ElMessageBox.alert(
      `迁移包恢复完成：设置 ${rc.settings || 0} 项、${fmtBackupCounts(rc)}；` +
      `凭据${res.restored_credentials ? '已还原' : '未覆盖'}；` +
      `本地元数据写入 ${res.meta_files_restored || 0} 个文件。页面即将刷新。`,
      '迁移恢复成功',
      { confirmButtonText: '刷新页面' },
    )
    location.reload()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') ElMessage.error(errMsg(e))
  } finally {
    bundleRestoring.value = false
    bundlePhase.value = ''
    bundleProgress.value = 0
  }
}
useUnsavedGuard(dirty)  // 有未保存更改时，离开页面弹窗确认
let suppressWatch = false

// Userbot 连接状态（接入方式 ≠ 网页轮询时展示）
const ub = reactive({ connected: false })
const loadUb = async () => {
  try { ub.connected = !!(await api.get('/api/tg/userbot/status')).data.connected } catch { /* ignore */ }
}
watch(() => f.value.tg_mode, (v) => { if (v && v !== 'polling') loadUb() })

const tgModeTip = computed(() => ({
  polling: '当前模式：网页轮询——只抓取接入方式为「网页轮询」的频道（公开频道，免 TG 账号）',
  userbot: '当前模式：Userbot——用 TG 小号实时监听接入方式为「Userbot」的频道（含私密频道）',
  both: '当前模式：两者——按每个频道自身的接入方式分流处理，互不影响',
}[f.value.tg_mode] || ''))

// 联动：关闭分享转存总开关时，下级「分享追更」自动关闭
const onToggleShareTransfer = (val) => {
  if (!val) {
    f.value.share_update_enabled = false
  }
}

watch([f, retryPreset, pollPreset, transferPreset], () => {
  if (!suppressWatch) dirty.value = true
}, { deep: true })

const load = async () => {
  suppressWatch = true
  loadError.value = ''
  try {
    f.value = (await api.get('/api/settings')).data
  } catch (e) {
    loadError.value = errMsg(e) || '设置加载失败'
    ElMessage.error(loadError.value)
    suppressWatch = false
    return
  }
  if (f.value.schedule_jitter_min === undefined) f.value.schedule_jitter_min = 5
  if (f.value.batch_rest_enabled === undefined) f.value.batch_rest_enabled = true
  if (f.value.batch_rest_count === undefined) f.value.batch_rest_count = 3
  if (f.value.batch_rest_min_min === undefined) f.value.batch_rest_min_min = 2
  if (f.value.batch_rest_max_min === undefined) f.value.batch_rest_max_min = 5
  if (f.value.health_check_interval_min === undefined) f.value.health_check_interval_min = 10
  if (f.value.dedup_cache_ttl_sec === undefined) f.value.dedup_cache_ttl_sec = 90
  if (!f.value.auto_backup_time) f.value.auto_backup_time = '04:00'
  // 回填三组预设：与已存值完全一致的档位；对不上时回落到标准档（保存时生效）
  const hit = (arr, cur, pick) => {
    const m = arr.find((p) => pick(p).every((n, i) => n === cur[i]))
    return (m || arr[1]).label
  }
  retryPreset.value = hit(RETRY_PRESETS, f.value.retry_backoff || [], (p) => p.sec)
  pollPreset.value = hit(POLL_PRESETS, [f.value.poll_interval_min, f.value.poll_interval_max],
                         (p) => [p.min, p.max])
  transferPreset.value = hit(TRANSFER_PRESETS, [f.value.transfer_interval_min, f.value.transfer_interval_max],
                             (p) => [p.min, p.max])
  if (f.value.tg_mode && f.value.tg_mode !== 'polling') loadUb()
  loadBundleEstimate()
  loaded.value = true
  await nextTick()
  suppressWatch = false
  dirty.value = false
}

const validateForm = () => {
  // 1. 每日转存上限 (1 ~ 50)
  const dl = Number(f.value.daily_limit)
  if (!Number.isInteger(dl) || dl < 1 || dl > 50) {
    ElMessage.warning('每日转存上限必须是 1 ~ 50 之间的整数')
    return false
  }

  // 2. 分享体积下限 (>= 0)
  const ms = Number(f.value.min_size_gb)
  if (isNaN(ms) || ms < 0 || ms > 1000) {
    ElMessage.warning('分享体积下限必须是大于等于 0 的有效数字（GB）')
    return false
  }

  // 3. 定时计划与开工随机扰动
  if (f.value.schedule_enabled) {
    const sStart = String(f.value.schedule_start || '').trim()
    const sEnd = String(f.value.schedule_end || '').trim()
    if (!sStart || !sEnd) {
      ElMessage.warning('定时计划的开始时间与结束时间不能为空')
      return false
    }
    if (sStart === sEnd) {
      ElMessage.warning('定时计划的开始时间与结束时间不能相同（如需全天候运行请关闭定时计划开关）')
      return false
    }
    const jm = Number(f.value.schedule_jitter_min)
    if (!Number.isInteger(jm) || jm < 0 || jm > 30) {
      ElMessage.warning('开工随机扰动必须是 0 ~ 30 之间的整数（分钟）')
      return false
    }
  }

  // 4. 拟人化连存小憩 (count 2~20, min 1~60, max 1~60, min <= max)
  if (f.value.batch_rest_enabled) {
    const rc = Number(f.value.batch_rest_count)
    if (!Number.isInteger(rc) || rc < 2 || rc > 20) {
      ElMessage.warning('连存小憩笔数必须是 2 ~ 20 之间的整数')
      return false
    }
    const rmin = Number(f.value.batch_rest_min_min)
    const rmax = Number(f.value.batch_rest_max_min)
    if (!Number.isInteger(rmin) || rmin < 1 || rmin > 60) {
      ElMessage.warning('最短小憩时间必须是 1 ~ 60 之间的整数（分钟）')
      return false
    }
    if (!Number.isInteger(rmax) || rmax < 1 || rmax > 60) {
      ElMessage.warning('最长小憩时间必须是 1 ~ 60 之间的整数（分钟）')
      return false
    }
    if (rmin > rmax) {
      ElMessage.warning(`小憩时间区间有误：最短小憩时间（${rmin} 分钟）不能大于最长小憩时间（${rmax} 分钟）`)
      return false
    }
  }

  // 5. 定时自动备份 (1 ~ 365, HH:MM, TG Bot 校验)
  if (f.value.auto_backup_enabled) {
    const token = String(f.value.notify_token || '').trim()
    const chatId = String(f.value.notify_chat_id || '').trim()
    if (!token || !chatId) {
      ElMessage.warning('开启「定时自动备份」必须先配置下方 Telegram Bot（Token 与 Chat ID），否则备份文件无法送达')
      return false
    }
    const bd = Number(f.value.auto_backup_days)
    if (!Number.isInteger(bd) || bd < 1 || bd > 365) {
      ElMessage.warning('自动备份间隔必须是 1 ~ 365 天之间的整数')
      return false
    }
    if (!f.value.auto_backup_time) {
      ElMessage.warning('请选择自动备份的推送时间')
      return false
    }
  }

  return true
}

const save = async () => {
  if (!validateForm()) return
  const backoff = (RETRY_PRESETS.find((p) => p.label === retryPreset.value) || RETRY_PRESETS[1]).sec
  const poll = (POLL_PRESETS.find((p) => p.label === pollPreset.value) || POLL_PRESETS[1])
  const trans = (TRANSFER_PRESETS.find((p) => p.label === transferPreset.value) || TRANSFER_PRESETS[1])
  try {
    const payload = { ...f.value }
    for (const k of Object.keys(payload)) {
      if (k.startsWith('auto_move_')) delete payload[k]
    }
    await api.put('/api/settings', {
      ...payload,
      retry_backoff: backoff,
      poll_interval_min: poll.min, poll_interval_max: poll.max,
      transfer_interval_min: trans.min, transfer_interval_max: trans.max,
    })
    dirty.value = false
    saveOk('设置已保存', '转存间隔、限额、开关等已即时生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const backupExport = async () => {
  try {
    const resp = await api.get('/api/backup/export', { responseType: 'blob' })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    const cd = resp.headers['content-disposition'] || ''
    const m = cd.match(/filename="?([^";]+)"?/)
    a.download = m ? m[1] : 'autosave_backup.json'
    a.click()
    URL.revokeObjectURL(url)
    saveOk('备份已导出', '包含设置/频道/规则/路由/黑名单/订阅/转存记录/自动整理/整理刮削/操作账本及待刮削/跳过任务')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const proxyTesting = ref(false)
const testProxy = async () => {
  proxyTesting.value = true
  try {
    const { data } = await api.post('/api/settings/proxy/test', { proxy: f.value.proxy })
    saveOk('代理可用', data.detail)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { proxyTesting.value = false }
}
const testNotify = async (token, chatId) => {
  testing.value = true
  try {
    await api.post('/api/settings/notify/test', {
      // 掩码/空串均回落已保存配置（?? 会把空字符串当有效值，属误用）
      token: (token && token !== SECRET_MASK) ? token : '',
      chat_id: chatId ?? f.value.notify_chat_id,
    })
    saveOk('测试消息已发送', '请到 Telegram 查看')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    testing.value = false
  }
}
// 扩展渠道测试：用已保存的配置向指定渠道发一条测试消息（需先保存设置）
const testChannel = async (ch) => {
  chTesting.value = ch
  try {
    const { data } = await api.post(`/api/settings/notify/test/${ch}`)
    saveOk(`${data.detail || '测试消息已发送'}`, '请打开对应 App 确认收到')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    chTesting.value = ''
  }
}
// 立即推送一次每日摘要（内容与定时推送一致），用于验证摘要功能是否正常
const digestTesting = ref(false)
const testDigest = async () => {
  digestTesting.value = true
  try {
    const { data } = await api.post('/api/settings/digest/test')
    if (data.ok) {
      saveOk('摘要已推送', '请到已配置的通知渠道查看')
    } else {
      const fail = Object.entries(data.detail || {}).filter(([, v]) => !v).map(([k]) => k).join('、')
      ElMessage.error(`推送失败：${fail} 未成功（未配置或发送失败，请检查通知配置）`)
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    digestTesting.value = false
  }
}
// 生成随机对外 API Token（32 位十六进制），点击「保存设置」后才落库生效
const genToken = () => {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  f.value.api_token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  ElMessage.success('已生成随机 Token，点击「保存设置」后生效')
}
onMounted(load)
</script>

<style scoped>
.section-card {
  border: 1px solid var(--saas-line);
  border-radius: 12px;
  padding: 0 18px 8px;
  margin-bottom: 16px;
  background: var(--subcard-bg);
  overflow: hidden;
}
.section-title {
  margin: 0 -18px 14px;
  padding: 11px 18px;
  background: var(--subcard-head-bg);
  border-bottom: 1px solid var(--saas-line);
  font-weight: 700; font-size: 15px; line-height: 1.35; color: var(--saas-text-main);
  display: flex; align-items: center; gap: 9px;
}
.section-title::before {
  content: ""; width: 4px; height: 15px; border-radius: 2px;
  background: var(--grad-brand); flex-shrink: 0;
}
/* 方案 A：小标题固定第一行，控件与说明在第二行铺满卡片宽度（桌面双栏与移动端共用） */
.settings-form :deep(.el-form-item) {
  margin-bottom: 14px;
  padding-bottom: 14px;
  border-bottom: 1px dashed rgba(126, 146, 196, .22);
}
.settings-form :deep(.el-form-item:last-child) {
  margin-bottom: 8px;
  padding-bottom: 0;
  border-bottom: none;
}
.settings-form :deep(.el-form-item__label) {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--saas-text-main);
  line-height: 1.4;
  height: auto;
  margin-bottom: 7px;
  padding-right: 0;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
}
.settings-form :deep(.el-form-item__label)::before {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary, #4f6bff);
  margin-right: 7px;
  flex-shrink: 0;
  box-shadow: 0 0 0 2px rgba(79, 107, 255, 0.20);
}
.settings-form :deep(.el-form-item__content) {
  min-width: 0;
  line-height: 1.5;
}
.settings-form :deep(.el-form-item__content > .tip) {
  margin-top: 6px;
  line-height: 1.55;
}
/* 左右两栏瀑布流布局：各分区卡片按高度自动分配到两列，窄屏回落单列 */
.settings-flow { column-count: 2; column-gap: 16px; }
.settings-flow > .section-card { break-inside: avoid; }
@media (max-width: 1100px) { .settings-flow { column-count: 1; } }
.tip { color: #999; font-size: var(--fs-caption); line-height: 1.55; }
/* 警示性提示文字：加粗 + 琥珀色，一眼可见（全局样式 .warn-text 的本地兜底） */
.warn-text { font-weight: 600; color: #b45309; }
.switch-row {
  width: 100%;
  display: flex; align-items: center; gap: 10px;
}
.switch-row :deep(.el-switch) { flex: none; }
.switch-row .tip { flex: 1; min-width: 0; }
.ub-inline { width: 100%; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ub-inline .tip { flex: 1 1 200px; min-width: 0; }
.range-row { width: 100%; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.range-tip { flex: 1 1 180px; min-width: 0; }
.input-btn-row :deep(.el-input) { flex: 1 1 220px; min-width: 0; width: auto; }
.input-btn-row :deep(.el-button) { flex: none; }
.range-row :deep(.el-input-number) { width: 132px; }
@media (max-width: 768px) {
  .section-card { padding: 0 14px 4px; }
  .section-title { margin: 0 -14px 12px; padding: 11px 14px; }
  .settings-form :deep(.el-form-item) { margin-bottom: 12px; padding-bottom: 12px; }
  .switch-row { gap: 10px; }
  .range-row { flex-wrap: wrap; gap: 8px; }
  .range-row :deep(.el-input-number) { width: 140px; }
  .input-btn-row :deep(.el-input) { flex: 1 1 160px; }
}
.bundle-est-box { width: 100%; background: #f8f9fb; border: 1px solid #ebeef5; border-radius: 6px; padding: 8px 12px; }
.bundle-est-metrics { font-size: 13px; color: #303133; line-height: 1.6; }
.bundle-opts { width: 100%; display: flex; flex-direction: column; gap: 8px; }
.bundle-label { font-size: 13px; color: var(--label-secondary, #606266); }
.bundle-capsule-radios {
  display: inline-flex;
  align-items: center;
}
.bundle-capsule-radios :deep(.el-radio-button) {
  display: inline-flex;
}
.bundle-capsule-radios :deep(.el-radio-button__inner) {
  height: 32px;
  line-height: 30px;
  padding: 0 14px;
  font-size: 12.5px;
  font-weight: 500;
  border-color: rgba(126, 146, 196, 0.40);
  background: rgba(255, 255, 255, 0.70);
  color: var(--saas-text-main);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.bundle-capsule-radios :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-top-left-radius: 8px;
  border-bottom-left-radius: 8px;
}
.bundle-capsule-radios :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-top-right-radius: 8px;
  border-bottom-right-radius: 8px;
}
.bundle-capsule-radios :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--grad-brand, linear-gradient(135deg, #4f6bff 0%, #704fff 100%)) !important;
  border-color: var(--el-color-primary) !important;
  color: #ffffff !important;
  font-weight: 600;
  box-shadow: -1px 0 0 0 var(--el-color-primary);
}
html.dark .bundle-capsule-radios :deep(.el-radio-button__inner) {
  background: rgba(20, 28, 48, 0.75);
  border-color: rgba(255, 255, 255, 0.18);
  color: #b7c4de;
}
html.dark .bundle-capsule-radios :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--grad-brand) !important;
  color: #ffffff !important;
}
.bundle-switch-group { display: flex; flex-direction: column; gap: 6px; }
.bundle-check { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: #606266; cursor: pointer; }
.bundle-progress-wrap { width: 100%; max-width: 460px; display: flex; flex-direction: column; gap: 4px; }
.sep { color: #999; }
</style>

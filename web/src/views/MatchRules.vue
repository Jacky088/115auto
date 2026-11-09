<template>
  <div>
    <!-- 匹配规则 -->
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>匹配规则（按优先级从小到大，首条命中生效）</span>
          <el-tag v-if="dirtyRules" type="danger" effect="plain" size="small">● 有未保存的更改</el-tag>
          <el-popconfirm title="确认恢复默认规则？当前自定义规则将被覆盖，目录映射保留。"
                         confirm-button-text="恢复默认" cancel-button-text="取消"
                         confirm-button-type="danger" width="260" @confirm="resetRules">
            <template #reference>
              <el-button>恢复默认</el-button>
            </template>
          </el-popconfirm>
          <el-button type="primary" plain @click="helpVisible = true">使用帮助</el-button>
          <el-button type="primary" @click="addRule">添加规则</el-button>
        </div>
      </template>
      <el-table :data="rules" size="small">
        <el-table-column label="优先级" width="90">
          <template #default="{ row }"><el-input-number v-model="row.priority" :min="1" controls-position="right" style="width: 80px" /></template>
        </el-table-column>
        <el-table-column label="正则" min-width="280">
          <template #default="{ row }"><el-input v-model="row.pattern" class="mono" placeholder="正则表达式" /></template>
        </el-table-column>
        <el-table-column label="分类" width="120">
          <template #default="{ row }">
            <el-select v-model="row.category" filterable allow-create>
              <el-option v-for="r in routes" :key="r.category" :label="r.category" :value="r.category" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }"><el-switch v-model="row.enabled" :active-value="1" :inactive-value="0" /></template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ $index }">
            <el-popconfirm title="确认删除该规则？保存后生效。" confirm-button-text="删除"
                           cancel-button-text="取消" confirm-button-type="danger" width="220"
                           @confirm="rules.splice($index, 1); dirtyRules = true">
              <template #reference><el-button type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div class="btn-row end" style="margin-top: 14px">
        <span v-if="dirtyRules" class="tip-text">修改需点击「保存规则」后生效</span>
        <el-button type="primary" :disabled="!dirtyRules" @click="saveRules">保存规则</el-button>
      </div>
    </el-card>

    <!-- 规则测试器 -->
    <el-card shadow="never" class="page-card">
      <template #header><span>规则测试器</span></template>
      <div class="toolbar">
        <el-input v-model="testText" placeholder="输入一条资源标题，模拟完整处理结果" style="flex: 1"
                  @keyup.enter="runTest" />
        <el-button type="primary" @click="runTest">测试</el-button>
      </div>
      <el-descriptions v-if="testResult" :column="1" border size="small" style="margin-top: 10px">
        <el-descriptions-item label="判定分类">{{ testResult.category || '（未命中规则）' }}</el-descriptions-item>
        <el-descriptions-item label="命中规则"><span class="mono">{{ testResult.matched_rule || '-' }}</span></el-descriptions-item>
        <el-descriptions-item label="黑名单">
          <el-tag v-if="testResult.blocked" type="danger" size="small">
            拦截（{{ testResult.block_rule_type }}: {{ testResult.block_rule_value }}）
          </el-tag>
          <el-tag v-else type="success" size="small">通过</el-tag>
        </el-descriptions-item>
        <el-descriptions-item v-if="testResult.disabled_kinds && testResult.disabled_kinds.length"
                              label="功能开关">
          <el-tag type="warning" size="small">
            {{ testResult.disabled_kinds.join('、') }}已关闭，实际不会处理
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="目标目录">
          <template v-if="testResult.blocked">-</template>
          <template v-else-if="testResult.offline_target_dir && testResult.target_kind === 'offline'">
            <span>{{ testResult.offline_target_dir }}</span>
            <el-tag size="small" type="warning" style="margin-left: 6px">云下载目录</el-tag>
          </template>
          <template v-else>{{ testResult.target_dir || '跳过（不转存）' }}</template>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 黑名单 -->
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>黑名单</span>
          <span style="color:#999; font-size:var(--fs-caption)">命中规则的资源在转存前被拦截，可复核放行</span>
        </div>
      </template>
      <el-tabs v-model="blTab" @tab-change="loadBlacklist">
        <el-tab-pane label="关键词" name="keyword" />
        <el-tab-pane label="频道" name="channel" />
        <el-tab-pane label="文件名正则" name="file_pattern" />
        <el-tab-pane label="命中记录" name="hits" />
      </el-tabs>

      <template v-if="blTab !== 'hits'">
        <div class="toolbar">
          <el-input v-model="blNewValue" style="flex: 1" type="textarea" :autosize="{ minRows: 1, maxRows: 8 }"
                    :placeholder="blPlaceholder" />
          <el-button type="primary" @click="addBlacklistItem">添加</el-button>
          <el-popconfirm v-if="blTab === 'keyword'" title="确认恢复默认关键词？只补齐缺失项，不会删除你添加的内容。"
                         confirm-button-text="恢复默认" cancel-button-text="取消" confirm-button-type="primary"
                         width="270" @confirm="blRestoreDefaults">
            <template #reference>
              <el-button>恢复默认</el-button>
            </template>
          </el-popconfirm>
          <el-button v-if="blTab === 'file_pattern'" @click="regexHelpVisible = true">正则说明</el-button>
        </div>
        <p style="color:#999; font-size:var(--fs-caption); margin-top: 0">
          {{ blHint }}
        </p>
        <el-table :data="blItems" size="small" empty-text="暂无条目">
          <el-table-column prop="value" label="内容" min-width="300" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.value }}</span></template>
          </el-table-column>
          <el-table-column prop="created_at" label="添加时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-popconfirm title="确认删除该条目？删除后不再拦截。" confirm-button-text="删除"
                             cancel-button-text="取消" confirm-button-type="danger" width="220"
                             @confirm="delBlacklistItem(row)">
                <template #reference><el-button type="danger" text>删除</el-button></template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template v-else>
        <p style="color:#999; font-size:var(--fs-caption); margin-top: 0; margin-bottom: 10px">
          被黑名单拦截的消息记录。点击内容可查看完整消息（分享链接已高亮）；若确认为误拦，可点击「放行」重新处理。
        </p>
        <el-table :data="blHits" size="small" empty-text="暂无拦截记录" v-loading="blHitsLoading">
          <el-table-column prop="created_at" label="时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="channel" label="频道" width="120">
            <template #default="{ row }"><span class="mono">@{{ row.channel }}</span></template>
          </el-table-column>
          <el-table-column label="拦截原因" width="200">
            <template #default="{ row }">
              <el-tag size="small" :type="stageTag(row.stage)" style="margin-right: 5px">
                {{ stageLabel(row.stage) }}
              </el-tag>
              <el-tag size="small" type="danger" effect="plain">{{ row.rule_value || row.rule_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="内容" min-width="300">
            <template #default="{ row }">
              <span class="hit-summary" @click="openDetail(row)" :title="row.text">
                {{ (row.text || '（无文本）').slice(0, 60) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <div class="hit-actions">
                <el-button text type="primary" @click="openDetail(row)">详情</el-button>
                <el-popconfirm title="确认放行？将忽略黑名单重新处理该消息。" confirm-button-text="放行"
                               cancel-button-text="取消" confirm-button-type="primary" width="240"
                               @confirm="releaseHit(row)">
                  <template #reference><el-button type="primary" text>放行</el-button></template>
                </el-popconfirm>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <!-- 使用帮助（匹配规则） -->
    <el-dialog v-model="helpVisible" title="匹配规则使用帮助" width="780px" top="4vh"
               class="help-dialog">
      <div class="help-body">
        <h4>一、工作原理</h4>
        <p>每条消息进来后，系统按以下流程处理：</p>
        <ol>
          <li><b>提取链接</b>：从消息正文（含「查看资源」按钮指向的页面）提取 115 分享链接或云下载链接（电驴/磁力）；</li>
          <li><b>规则匹配</b>：用消息标题从上到下逐条尝试匹配规则，<b>第一条命中的规则决定分类</b>（优先级数字越小越靠前）；</li>
          <li><b>目录路由</b>：按分类查「转存目录」页的映射表——<b>分享转存进「分享转存目录」，云下载进「云下载目录」</b>（云下载目录留空则与分享转存相同）；</li>
          <li><b>黑名单检查</b>：命中本页黑名单的丢弃，其余入队转存。</li>
        </ol>
        <p class="eg">例：某资源命中「全N集」规则 → 分类<b>电视剧</b> → 分享转存进
          <code>/媒体库/电视剧</code>；同一资源若是 ED2K 云下载链接，且该分类的云下载目录填了
          <code>/云下载/电视剧</code>，则进云下载目录（留空则与分享转存同目录）。</p>

        <h4>二、内置规则说明</h4>
        <el-table :data="builtinRules" size="small" border>
          <el-table-column prop="category" label="分类" width="80" />
          <el-table-column label="匹配特征" min-width="200">
            <template #default="{ row }"><span class="mono" style="font-size:var(--fs-caption)">{{ row.desc }}</span></template>
          </el-table-column>
          <el-table-column label="标题示例" min-width="180">
            <template #default="{ row }">{{ row.example }}</template>
          </el-table-column>
        </el-table>
        <p class="tip">字幕组列表无法穷举所有组名；未覆盖的资源可按下方方法自定义。</p>

        <h4>三、不够用时：添加自定义规则</h4>
        <p>点击「添加规则」→ 填写正则 → 选择（或输入）分类 → 设置优先级 → <b>点击「保存规则」</b>。用下方测试器验证后再投入运行。</p>
        <el-table :data="regexTips" size="small" border>
          <el-table-column prop="frag" label="正则片段" width="180">
            <template #default="{ row }"><code>{{ row.frag }}</code></template>
          </el-table-column>
          <el-table-column prop="meaning" label="含义" />
          <el-table-column prop="demo" label="示例" width="200" />
        </el-table>
        <p class="eg">自定义示例：想识别微短剧 → 添加规则 <code>(短剧|微短剧|精品短剧)</code> → 分类选「电视剧」；
          想让某个发布组的作品全部归动漫 → 添加 <code>\[该发布组名\]</code> → 分类选「动漫」 → 优先级设 9（排在内置字幕组规则之前）。</p>

        <h4>四、识别不了的资源怎么办（未分类兜底）</h4>
        <p>所有规则都没命中的资源按「未分类动作」处理（「转存目录」页右上角）：</p>
        <ul>
          <li><b>跳过</b>（默认）：不转存，转存记录里标记为「已跳过」，不会打乱已整理的目录结构；</li>
          <li><b>转入指定目录</b>：在下拉框直接输入一个路径（如 <code>/待整理</code>），无法识别的资源统一进这里，
            之后你在 115 网盘里手动归档；也可以为它添加自定义规则（如 <code>短剧|微短剧</code> → 分类「电视剧」）让它以后自动分类。</li>
        </ul>

        <h4>五、注意事项</h4>
        <ul>
          <li>规则只决定<b>分类</b>；分类 → 具体目录的映射在「转存目录」页配置，两类链接各自生效；</li>
          <li>优先级数字越小越先匹配；新增自定义规则建议从 50 开始，想「抢在内置规则前」就设得更小；</li>
          <li>正则默认忽略大小写；写错语法的规则会被自动跳过，不会影响其他规则；</li>
          <li>修改规则后需点击「保存规则」生效，并用测试器实际验证；</li>
          <li>「恢复默认」会把匹配规则重置为最新内置规则集（不影响你配置的目录映射），升级后内置规则有更新时可用它一键升级。</li>
        </ul>
      </div>
      <template #footer>
        <el-button type="primary" @click="helpVisible = false">我知道了</el-button>
      </template>
    </el-dialog>

    <!-- 拦截详情弹窗 -->
    <el-dialog v-model="detailVisible" title="拦截详情" width="640px" top="6vh">
      <el-descriptions :column="2" border size="small" v-if="detailRow">
        <el-descriptions-item label="时间">{{ fmtTime(detailRow.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="来源频道">
          <span class="mono">@{{ detailRow.channel }}</span> · 消息 #{{ detailRow.message_id }}
        </el-descriptions-item>
        <el-descriptions-item label="拦截环节">{{ stageLabel(detailRow.stage) }}</el-descriptions-item>
        <el-descriptions-item label="命中规则">
          <el-tag size="small" type="danger" effect="plain">
            {{ detailRow.rule_type }}: {{ detailRow.rule_value }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <div class="detail-content" v-html="detailHtml"></div>
      <template #footer>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <el-button text @click="copyLinks">复制消息中的资源链接</el-button>
          <div>
            <el-button @click="detailVisible = false">关闭</el-button>
            <el-popconfirm title="确认放行？将忽略黑名单重新处理该消息。" confirm-button-text="放行"
                           cancel-button-text="取消" confirm-button-type="primary" width="240"
                           @confirm="releaseFromDetail">
              <template #reference>
                <el-button type="primary">放行并转存</el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 文件名正则帮助 -->
    <el-dialog v-model="regexHelpVisible" title="文件名正则说明" width="780px" top="4vh">
      <div class="help-body">
        <h4>一、作用机制</h4>
        <p>转存前会先读取分享内的<b>文件列表</b>：只要任意一个文件名命中你设置的正则，
          <b>整个分享就会被跳过</b>（记录为「黑名单」状态，可到命中记录里放行）。适合过滤广告文件、样本、垃圾附件等。</p>
        <p class="eg">例：设置 <code>\.url$</code> 后，分享内含有「更多资源点这里.url」这类广告文件的分享会被整体拦截。</p>

        <h4>二、语法要点</h4>
        <el-table :data="blRegexTips" size="small" border>
          <el-table-column prop="frag" label="正则片段" width="200">
            <template #default="{ row }"><code>{{ row.frag }}</code></template>
          </el-table-column>
          <el-table-column prop="meaning" label="含义" />
        </el-table>

        <h4>三、常用配方</h4>
        <el-table :data="blRecipes" size="small" border>
          <el-table-column prop="re" label="正则" min-width="220">
            <template #default="{ row }"><code>{{ row.re }}</code></template>
          </el-table-column>
          <el-table-column prop="use" label="用途" />
        </el-table>

        <h4>四、注意事项</h4>
        <ul>
          <li>正则默认<b>忽略大小写</b>；一条正则写错语法不影响其他规则；</li>
          <li>拦截粒度是<b>整个分享</b>——正则越宽松，误拦越多。建议从具体后缀（如 <code>\.url$</code>）起步，观察命中记录后再逐步放宽；</li>
          <li>确认误拦后，到「命中记录」里点「放行」即可重新转存该分享。</li>
        </ul>
      </div>
      <template #footer>
        <el-button type="primary" @click="regexHelpVisible = false">我知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, fmtTime, saveOk, highlightLinks } from '../api'
import { useUnsavedGuard } from '../composables/unsavedGuard'

// ---------- 匹配规则 ----------
const rules = ref([])
const routes = ref([])   // 供规则行的"分类"下拉取可选分类
const dirtyRules = ref(false)
const helpVisible = ref(false)
const testText = ref('')
const testResult = ref(null)
let suppressWatch = false

const builtinRules = [
  { category: '少儿', desc: '含 少儿 / 儿童 / 幼儿 / 亲子 / 启蒙（优先于其他所有规则）', example: '小猪佩奇 少儿动画 全26集' },
  { category: '动漫', desc: '[字幕组标签] 开头（ANi/LoliHouse/SweetSub/桜都/NC-Raws 等常见组）', example: '[ANi] 我推的孩子 S02E01' },
  { category: '动漫', desc: '含 新番 / 番剧 / 季度合集 / 国创 / 动画', example: '某某季度合集 [合集]' },
  { category: '动漫', desc: '含 OVA / OAD / 剧场版', example: '咒术回战 剧场版 1080p' },
  { category: '纪录片', desc: '含 纪录片 / documentary / BBC. / NHK. / Discovery / 国家地理', example: '行星地球 BBC.2006.1080p' },
  { category: '电视剧', desc: '季集格式 S01E05（数字1-2位季+1-3位集）', example: '琅琊榜.S01E01.2160p' },
  { category: '电视剧', desc: '第 N 集 / 第 N 话', example: '潜伏 第15集 4K修复' },
  { category: '电视剧', desc: '全N集 / 全集 / 完结 / 更新至N集', example: '甄嬛传（2011）全76集' },
  { category: '电视剧', desc: 'Season N 或 EP+N 集号', example: 'Friends.S03 或 某剧 EP01' },
  { category: '综艺', desc: '第 N 期', example: '奔跑吧 第12期 1080p' },
  { category: '综艺', desc: '年份季格式 S2024E15（4位数字季为综艺特征）', example: 'RunningMan.S2024E15' },
  { category: '综艺', desc: '含 综艺 / 真人秀 / 演唱会 / 晚会', example: '某某演唱会 全场版' },
  { category: '电影', desc: '括号或点分隔的年份 (2023) / .2023.', example: '阿凡达.(2009).2160p' },
  { category: '电影', desc: '画质标识 1080p / 2160p / 720p / 4K / BluRay / REMUX / WEB-DL / HDTV / HDR', example: '流浪地球2.4K.HDR' },
]

const regexTips = [
  { frag: '第\\d+集', meaning: '"第"+若干数字+"集"', demo: '第25集' },
  { frag: '[Ss]\\d{2}[Ee]\\d{2}', meaning: '季集格式', demo: 'S01E05' },
  { frag: '\\[组名\\]', meaning: '方括号字面量（[]需转义）', demo: '[某字幕组]' },
  { frag: 'a|b', meaning: '或', demo: '短剧|微短剧' },
  { frag: '\\d+', meaning: '若干数字', demo: '全12集 的 12' },
  { frag: '(?i)', meaning: '忽略大小写（系统已默认忽略）', demo: '(?i)bluray' },
  { frag: '^开头', meaning: '^ 限定从标题开头匹配', demo: '^\\[ANi\\]' },
]

watch(rules, () => {
  if (!suppressWatch) dirtyRules.value = true
}, { deep: true })

const loadRules = async () => {
  const { data } = await api.get('/api/rules')
  suppressWatch = true
  rules.value = data.rules
  routes.value = data.routes
  await nextTick()
  suppressWatch = false
  dirtyRules.value = false
}

const addRule = () => {
  rules.value.push({ pattern: '', category: '', priority: 50, enabled: 1 })
  dirtyRules.value = true
}
const saveRules = async () => {
  try {
    await api.put('/api/rules', { rules: rules.value })
    dirtyRules.value = false
    saveOk('规则已保存', '匹配规则更改已生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const resetRules = async () => {
  try {
    const { data } = await api.post('/api/rules/reset')
    await loadRules()
    saveOk('已恢复默认', `已载入 ${data.count} 条最新内置规则，缺失的默认分类目录已补齐`)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const runTest = async () => {
  if (!testText.value.trim()) return
  testResult.value = (await api.post('/api/rules/test', { text: testText.value })).data
}

// ---------- 黑名单 ----------
const blTab = ref('keyword')
const blItems = ref([])
const blHits = ref([])
const blHitsLoading = ref(false)
const blNewValue = ref('')
const detailVisible = ref(false)
const detailRow = ref(null)
const regexHelpVisible = ref(false)

const blPlaceholderMap = {
  keyword: '标题包含即拦截，如：预告、枪版（可一次粘贴多行批量添加）',
  channel: '频道名，如：some_channel',
  file_pattern: '正则表达式，如：.*\\.txt$',
}
const blPlaceholder = ref(blPlaceholderMap.keyword)
const blHints = {
  keyword: '消息标题命中关键词 → 整条消息丢弃（含所有链接）',
  channel: '频道黑名单 → 该频道的所有消息直接忽略',
  file_pattern: '分享内文件名命中正则 → 该分享整体跳过（转存前检查）',
}
const blHint = ref(blHints.keyword)

const blRegexTips = [
  { frag: '\\.txt$', meaning: '以 .txt 结尾（$ 表示结尾）', },
  { frag: '\\.(jpg|png)$', meaning: '以 .jpg 或 .png 结尾', },
  { frag: 'sample|proof', meaning: '含 sample 或 proof（样本/截图文件）', },
  { frag: '(?i)', meaning: '忽略大小写（系统已默认忽略）', },
  { frag: '广告|更多资源', meaning: '含这些字样的文件名', },
]
const blRecipes = [
  { re: '\\.(url|website|lnk)$', use: '拦截推广快捷方式（常见于广告分享）' },
  { re: '\\.(txt|nfo|doc|pdf)$', use: '拦截说明文档类小文件（谨慎：正规分享也可能带说明文件）' },
  { re: '^sample|\\.sample$', use: '拦截样本视频（sample 片段）' },
  { re: '推广|广告|加微信|公众号', use: '拦截中文广告命名的文件' },
]

const stageLabel = (s) => ({ message: '消息级', share_info: '内容级' }[s] || s)
const stageTag = (s) => ({ message: 'info', share_info: 'warning' }[s] || 'info')

const detailHtml = computed(() => highlightLinks(detailRow.value?.text || ''))

const loadBlacklist = async () => {
  if (blTab.value === 'hits') {
    blHitsLoading.value = true
    try {
      blHits.value = (await api.get('/api/blacklist/hits')).data
    } finally {
      blHitsLoading.value = false
    }
  } else {
    blItems.value = (await api.get(`/api/blacklist/${blTab.value}`)).data
    blPlaceholder.value = blPlaceholderMap[blTab.value]
    blHint.value = blHints[blTab.value]
  }
}
const addBlacklistItem = async () => {
  if (!blNewValue.value.trim()) return
  try {
    await api.post(`/api/blacklist/${blTab.value}`, { value: blNewValue.value })
    blNewValue.value = ''
    ElMessage.success('已添加，立即生效')
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const blRestoreDefaults = async () => {
  try {
    const { data } = await api.post('/api/blacklist/restore-defaults')
    saveOk('已恢复默认', `已补齐 ${data.added} 条缺失的默认关键词`)
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const delBlacklistItem = async (row) => {
  try {
    await api.delete(`/api/blacklist/${blTab.value}/${row.id}`)
    ElMessage.success('已删除')
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const releaseHit = async (row) => {
  try {
    await api.post(`/api/blacklist/hits/${row.id}/release`)
    ElMessage.success('已放行，将忽略黑名单并重新处理该消息')
    loadBlacklist()
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const openDetail = (row) => {
  detailRow.value = row
  detailVisible.value = true
}
const releaseFromDetail = async () => {
  if (!detailRow.value) return
  await releaseHit(detailRow.value)
  detailVisible.value = false
}
const copyLinks = async () => {
  const text = detailRow.value?.text || ''
  const re = /(https?:\/\/[^\s]+|115:\/\/[^\s]+|ed2k:\/\/\|file\|[^|\s]+\|\d+\|[0-9A-Fa-f]{32}\|\/?|magnet:\?xt=urn:btih:[^\s]+)/gi
  const links = [...new Set(text.match(re) || [])]
  if (!links.length) {
    ElMessage.warning('该消息中没有资源链接')
    return
  }
  try {
    await navigator.clipboard.writeText(links.join('\n'))
    ElMessage.success(`已复制 ${links.length} 条链接`)
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

useUnsavedGuard(dirtyRules)  // 规则表有未保存更改时，离开页面弹窗确认（黑名单为即时生效，无需保存）
onMounted(() => { loadRules(); loadBlacklist() })
</script>

<style scoped>
.tip-text { color: #e6a23c; font-size: var(--fs-caption); }
/* 命中记录操作列：详情 / 放行 左右排列，不换行 */
.hit-actions { display: flex; align-items: center; gap: 2px; white-space: nowrap; }
.hit-actions :deep(.el-button) { margin-left: 0; padding: 4px 8px; }
.hit-summary { cursor: pointer; color: #3a4661; font-size: var(--fs-caption); }
.hit-summary:hover { color: var(--el-color-primary); text-decoration: underline; }
.detail-content {
  margin-top: 14px; padding: 12px 14px; border-radius: 10px;
  background: #f6f9ff; border: 1px solid #e9eefb;
  font-size: var(--fs-body); line-height: 1.9; color: #3a4661;
  white-space: pre-wrap; word-break: break-word;
  max-height: 42vh; overflow-y: auto;
}
</style>

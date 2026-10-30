<template>
  <div>
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0">
          <span>分类 → 目录 映射</span>
          <el-input v-model="newCat" placeholder="新分类名" style="width: 140px" @keyup.enter="addRoute" />
          <el-button @click="addRoute">添加分类</el-button>
          <el-tooltip content="无法识别分类的资源：跳过，或输入任意目录统一存放（如 /待整理），输入后回车确认">
            <span style="color:#999; font-size:12px; margin-left: auto">
              未分类动作：
              <el-select v-model="unclassifiedAction" style="width: 170px" filterable allow-create
                         default-first-option @change="saveRoutes">
                <el-option label="跳过（不转存）" value="skip" />
                <el-option label="/待整理" value="/待整理" />
              </el-select>
            </span>
          </el-tooltip>
        </div>
      </template>
      <el-table :data="routes" size="small">
        <el-table-column prop="category" label="分类" width="120" />
        <el-table-column label="分享转存目录" min-width="220">
          <template #default="{ row }">
            <el-input v-model="row.target_dir" size="small" placeholder="/媒体库/xxx 或 skip"
                      @change="saveRoutes" />
          </template>
        </el-table-column>
        <el-table-column label="云下载目录" min-width="220">
          <template #default="{ row }">
            <el-input v-model="row.offline_dir" size="small" placeholder="留空 = 与分享转存相同，或 /xxx、skip"
                      @change="saveRoutes" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ $index }">
            <el-popconfirm title="确认删除该分类映射？改动立即生效。" confirm-button-text="删除"
                           cancel-button-text="取消" confirm-button-type="danger" width="230"
                           @confirm="routes.splice($index, 1); saveRoutes()">
              <template #reference><el-button size="small" type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

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
          <template #default="{ row }"><el-input-number v-model="row.priority" size="small" :min="1" controls-position="right" style="width: 80px" /></template>
        </el-table-column>
        <el-table-column label="正则" min-width="280">
          <template #default="{ row }"><el-input v-model="row.pattern" size="small" class="mono" placeholder="正则表达式" /></template>
        </el-table-column>
        <el-table-column label="分类" width="120">
          <template #default="{ row }">
            <el-select v-model="row.category" size="small" filterable allow-create>
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
              <template #reference><el-button size="small" type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 14px; display: flex; justify-content: flex-end; align-items: center; gap: 10px">
        <span v-if="dirtyRules" class="tip-text">修改需点击保存后才生效</span>
        <el-button type="primary" :disabled="!dirtyRules" @click="saveRules">保存规则</el-button>
      </div>
    </el-card>

    <!-- 规则使用帮助 -->
    <el-dialog v-model="helpVisible" title="分类规则使用帮助" width="780px" top="4vh"
               class="help-dialog">
      <div class="help-body">
        <h4>一、工作原理</h4>
        <p>每条消息进来后，系统按以下流程处理：</p>
        <ol>
          <li><b>提取链接</b>：从消息正文（含"查看资源"按钮指向的页面）提取 115 分享链接或云下载链接（电驴/磁力）；</li>
          <li><b>规则匹配</b>：用消息标题从上到下逐条尝试匹配规则，<b>第一条命中的规则决定分类</b>（优先级数字越小越靠前）；</li>
          <li><b>目录路由</b>：按分类查上方「分类 → 目录映射」——<b>分享转存进「分享转存目录」，云下载进「云下载目录」</b>（云下载目录留空则与分享转存相同）；</li>
          <li><b>黑名单检查</b>：命中黑名单的丢弃，其余入队转存。</li>
        </ol>
        <p class="eg">例：某资源命中"全N集"规则 → 分类<b>电视剧</b> → 分享转存进
          <code>/媒体库/电视剧</code>；同一资源若是 ED2K 云下载链接，且该分类的云下载目录填了
          <code>/云下载/电视剧</code>，则进云下载目录（留空则与分享转存同目录）。</p>

        <h4>二、内置规则说明</h4>
        <el-table :data="builtinRules" size="small" border>
          <el-table-column prop="category" label="分类" width="80" />
          <el-table-column label="匹配特征" min-width="200">
            <template #default="{ row }"><span class="mono" style="font-size:12px">{{ row.desc }}</span></template>
          </el-table-column>
          <el-table-column label="标题示例" min-width="180">
            <template #default="{ row }">{{ row.example }}</template>
          </el-table-column>
        </el-table>
        <p class="tip">字幕组列表无法穷举所有组名；未覆盖的资源可按下方方法自定义。</p>

        <h4>三、不够用时：添加自定义规则</h4>
        <p>点击「添加规则」→ 填写正则 → 选择（或输入）分类 → 设置优先级 → <b>点「保存规则」</b>。用下方测试器验证后再投入运行。</p>
        <el-table :data="regexTips" size="small" border>
          <el-table-column prop="frag" label="正则片段" width="180">
            <template #default="{ row }"><code>{{ row.frag }}</code></template>
          </el-table-column>
          <el-table-column prop="meaning" label="含义" />
          <el-table-column prop="demo" label="示例" width="200" />
        </el-table>
        <p class="eg">自定义示例：想识别微短剧 → 添加规则 <code>(短剧|微短剧|精品短剧)</code> → 分类选"电视剧"；
          想让某个发布组的作品全部归动漫 → 添加 <code>\[该发布组名\]</code> → 分类选"动漫" → 优先级设 9（排在内置字幕组规则之前）。</p>

        <h4>四、识别不了的资源怎么办（未分类兜底）</h4>
        <p>所有规则都没命中的资源按「未分类动作」处理（映射表右上角）：</p>
        <ul>
          <li><b>跳过</b>（默认）：不转存，转存记录里标记为"已跳过"，不会弄乱你的目录；</li>
          <li><b>转入指定目录</b>：在下拉框直接输入一个路径（如 <code>/待整理</code>），无法识别的资源统一进这里，
            之后你在 115 网盘里手动归档；也可以为它添加自定义规则（如 <code>短剧|微短剧</code> → 分类"电视剧"）让它以后自动分类。</li>
        </ul>

        <h4>五、注意事项</h4>
        <ul>
          <li>规则只决定<b>分类</b>；分类 → 具体目录的映射在上方映射表配置，两类链接各自生效；</li>
          <li>优先级数字越小越先匹配；新增自定义规则建议从 50 开始，想"抢在内置规则前"就设得更小；</li>
          <li>正则默认忽略大小写；写错语法的规则会被自动跳过，不会影响其他规则；</li>
          <li>改完规则记得点「保存规则」，并用下方测试器实际验证；</li>
          <li>「恢复默认」会把匹配规则重置为最新内置规则集（不影响你配置的目录映射），升级后内置规则有更新时可用它一键升级。</li>
        </ul>
      </div>
      <template #footer>
        <el-button type="primary" @click="helpVisible = false">我知道了</el-button>
      </template>
    </el-dialog>

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
          <template v-else>{{ testResult.target_dir || '跳过不转存' }}</template>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg, saveOk } from '../api'

const routes = ref([])
const rules = ref([])
const unclassifiedAction = ref('skip')
const newCat = ref('')
const testText = ref('')
const testResult = ref(null)
const dirtyRules = ref(false)
const helpVisible = ref(false)
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

const load = async () => {
  const { data } = await api.get('/api/rules')
  suppressWatch = true
  routes.value = data.routes
  rules.value = data.rules
  unclassifiedAction.value = data.unclassified_action
  await nextTick()
  suppressWatch = false
  dirtyRules.value = false
}

// ---- 路由映射：改动立即自动保存 ----
const addRoute = async () => {
  if (!newCat.value.trim()) return
  routes.value.push({ category: newCat.value.trim(), target_dir: `/媒体库/${newCat.value.trim()}`, offline_dir: '' })
  newCat.value = ''
  await saveRoutes()
}
const saveRoutes = async () => {
  try {
    await api.put('/api/rules', {
      routes: routes.value,
      unclassified_action: unclassifiedAction.value,
    })
    saveOk('目录映射已保存', '分类目录更改已立即生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}

// ---- 匹配规则：需手动点击保存 ----
const addRule = () => {
  rules.value.push({ pattern: '', category: '', priority: 50, enabled: 1 })
  dirtyRules.value = true
}
const saveRules = async () => {
  try {
    await api.put('/api/rules', {
      rules: rules.value,
      routes: routes.value,
      unclassified_action: unclassifiedAction.value,
    })
    dirtyRules.value = false
    saveOk('规则已保存', '匹配规则更改已生效')
  } catch (e) { ElMessage.error(errMsg(e)) }
}
const resetRules = async () => {
  try {
    const { data } = await api.post('/api/rules/reset')
    await load()
    saveOk('已恢复默认规则', `已载入 ${data.count} 条最新内置规则，缺失的默认分类目录已补齐`)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

const runTest = async () => {
  if (!testText.value.trim()) return
  testResult.value = (await api.post('/api/rules/test', { text: testText.value })).data
}
onMounted(load)
</script>

<style scoped>
.tip-text { color: #e6a23c; font-size: 12px; }
</style>

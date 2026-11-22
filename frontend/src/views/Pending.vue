<template>
  <div>
    <el-alert class="mb" type="warning" :closable="false" show-icon
      title="待判队列：规则维护期间读数保留为待判（非合格）；规则恢复后调度器先处理实时高风险，再按法定时限与时间顺序补判积压。" />

    <el-row :gutter="12" class="mb">
      <el-col :span="6"><el-card><div class="num">{{ sum.windows ?? '-' }}</div><div class="lbl">待判窗口</div></el-card></el-col>
      <el-col :span="6"><el-card><div class="num warn">{{ sum.pending ?? '-' }}</div><div class="lbl">待补判条目</div></el-card></el-col>
      <el-col :span="6"><el-card><div class="num ok">{{ sum.rejudged ?? '-' }}</div><div class="lbl">已补判</div></el-card></el-col>
      <el-col :span="6"><el-card><div class="num" :class="{ bad: sum.blocked }">{{ sum.blocked ?? '-' }}</div><div class="lbl">撤销阻塞</div></el-card></el-col>
    </el-row>

    <el-card class="mb">
      <template #header>
        <div class="head">
          <span>待判窗口（输入完整性 · 缺失规则集合）</span>
          <div>
            <el-radio-group v-model="strategy" size="small" @change="loadWindows">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="snapshot">封存快照</el-radio-button>
              <el-radio-button value="event_log">事件日志</el-radio-button>
            </el-radio-group>
            <el-button size="small" type="primary" style="margin-left:8px" :loading="busy" @click="rejudge">立即补判</el-button>
          </div>
        </div>
      </template>
      <el-table :data="windows" size="small" border>
        <el-table-column prop="window_key" label="窗口" width="260" show-overflow-tooltip />
        <el-table-column prop="factor" label="因子" width="90" />
        <el-table-column prop="partition_key" label="分区" width="100" />
        <el-table-column prop="risk_level" label="风险级" width="80" />
        <el-table-column label="路线" width="110">
          <template #default="{ row }">{{ row.strategy === 'snapshot' ? '封存快照' : '事件日志' }}</template>
        </el-table-column>
        <el-table-column prop="total" label="总数" width="70" />
        <el-table-column prop="pending" label="待判" width="70" />
        <el-table-column prop="rejudged" label="已补判" width="80" />
        <el-table-column prop="blocked" label="阻塞" width="70" />
        <el-table-column label="输入完整性" width="170">
          <template #default="{ row }">
            <el-tooltip :content="row.completeness_basis || ''" placement="top">
              <el-progress :percentage="Math.round((row.completeness || 0) * 100)" :stroke-width="10"
                :status="row.completeness >= 1 ? 'success' : 'warning'" />
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="缺失规则" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="m in row.missing_rules" :key="m.rule_code" size="small" type="danger" style="margin-right:4px">
              {{ m.rule_code }}<span v-if="m.version"> v{{ m.version }}</span>
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="legal_deadline" label="法定时限(UTC)" width="180" />
      </el-table>
    </el-card>

    <el-row :gutter="12">
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>旧新规则差异（缺失时版本 → 补判当前版本）</span></template>
          <el-table :data="diffs" size="small" border max-height="320">
            <el-table-column prop="device_point" label="点位" width="110" />
            <el-table-column prop="rule_code" label="规则" width="150" />
            <el-table-column prop="old_version" label="旧版本" width="80" />
            <el-table-column label="新版本" width="90">
              <template #default="{ row }">
                <span :class="{ chg: row.changed }">{{ row.new_version ?? '-' }}</span>
                <el-tag v-if="row.current_revoked" type="danger" size="small" style="margin-left:4px">撤销</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>补判运行（处理顺序：实时高风险 → 法定时限 → 时间）</span></template>
          <el-table :data="runs" size="small" border max-height="320">
            <el-table-column prop="id" label="#" width="60" />
            <el-table-column prop="processed" label="处理" width="70" />
            <el-table-column prop="alerted" label="告警重演" width="90" />
            <el-table-column prop="blocked" label="阻塞" width="70" />
            <el-table-column label="顺序预览" min-width="200">
              <template #default="{ row }">
                <span v-for="p in row.order_preview.slice(0, 4)" :key="p.pending_id" class="chip"
                  :class="{ hi: p.live_high }">{{ p.device_point }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="blockedItems.length">
      <template #header><span>撤销阻塞项（需显式指定替代版本，禁止静默覆盖）</span></template>
      <el-table :data="blockedItems" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="device_point" label="点位" width="120" />
        <el-table-column prop="blocked_reason" label="阻塞原因" show-overflow-tooltip />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="resolve(row)">显式处理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const sum = ref({})
const windows = ref([])
const diffs = ref([])
const runs = ref([])
const blockedItems = ref([])
const strategy = ref('')
const busy = ref(false)

function statusType(s) {
  return s === 'rejudged' ? 'success' : s === 'blocked' ? 'danger' : 'warning'
}
function statusText(s) {
  return s === 'rejudged' ? '已补判' : s === 'blocked' ? '阻塞' : '待判'
}

async function loadWindows() {
  const params = strategy.value ? { strategy: strategy.value } : {}
  const [w, s] = await Promise.all([
    api.get('/pending/windows'),
    api.get('/pending/summary'),
  ])
  windows.value = w.data.filter((x) => !strategy.value || x.strategy === strategy.value)
  sum.value = s.data
  const b = await api.get('/pending/items', { params: { status: 'blocked', page_size: 50 } })
  blockedItems.value = b.data.items
}
async function loadStatic() {
  const [d, r] = await Promise.all([api.get('/pending/rule-diffs'), api.get('/rejudge/runs')])
  diffs.value = d.data
  runs.value = r.data
}
async function rejudge() {
  busy.value = true
  try {
    const { data } = await api.post('/pending/rejudge', { strategy: strategy.value || null })
    const g = (data.backlog_groups || []).map((x) => `${x.factor}/${x.window_bucket.slice(11, 16)}/${x.risk_level}×${x.count}`).join('，')
    ElMessage.success(`补判完成：处理 ${data.processed}，告警重演 ${data.alerted}，阻塞 ${data.blocked}${g ? '｜积压分组：' + g : ''}`)
    await loadWindows()
    await loadStatic()
  } finally {
    busy.value = false
  }
}
async function resolve(row) {
  const { value } = await ElMessageBox.prompt(
    `为阻塞项 #${row.id} 显式指定替代规则版本（JSON，如 {"R-NH3-RANGE": 2}）`,
    '显式处理撤销阻塞',
    { inputValue: '{}' }
  )
  let versions = {}
  try {
    versions = JSON.parse(value)
  } catch {
    return ElMessage.error('JSON 无效')
  }
  await api.post(`/pending/${row.id}/resolve`, { rule_versions: versions })
  ElMessage.success('已显式处理，等待补判')
  loadWindows()
}
onMounted(() => {
  loadWindows()
  loadStatic()
})
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.num { font-size: 26px; font-weight: 600; color: #303133; }
.num.ok { color: #67c23a; }
.num.warn { color: #e6a23c; }
.num.bad, .bad { color: #f56c6c; font-weight: 600; }
.lbl { color: #909399; font-size: 12px; margin-top: 4px; }
.chg { color: #e6a23c; font-weight: 600; }
.chip { display: inline-block; background: #eef2f7; border-radius: 3px; padding: 0 6px; margin-right: 4px; font-size: 12px; }
.chip.hi { background: #fde2e2; color: #c45656; }
</style>

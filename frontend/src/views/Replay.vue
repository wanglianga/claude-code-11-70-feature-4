<template>
  <div>
    <el-alert class="mb" type="info" :closable="false" show-icon
      title="回放沙箱：对只读虚构报送样本计算质量结论与告警影响。发现已报送读数缺陷时创建待补正事项，但保留原报送范围、回执与当时依据；遇到规则中途撤销会标出受影响输入，禁止静默覆盖。" />

    <el-card class="mb">
      <template #header>
        <div class="head">
          <span>只读虚构报送样本（不依赖任何真实报送能力，不可修改）</span>
          <el-tag type="info" size="small">READ-ONLY</el-tag>
        </div>
      </template>
      <el-table :data="samples.batches" size="small" border>
        <el-table-column prop="batch_code" label="批次" width="170" />
        <el-table-column prop="regulator" label="监管机构(虚构)" width="200" />
        <el-table-column prop="scope" label="报送范围" show-overflow-tooltip />
        <el-table-column prop="reading_count" label="读数" width="70" />
        <el-table-column label="回执" width="200">
          <template #default="{ row }">
            <div v-if="row.receipt">{{ row.receipt.receipt_no }}</div>
            <div class="sub">{{ row.receipt?.received_by }} · {{ row.receipt?.status }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="submitted_at" label="报送时间(UTC)" width="180" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" type="primary" :loading="busy" @click="runReplay(row.batch_code)">回放</el-button>
            <el-button size="small" link @click="showReadings(row.batch_code)">原始读数</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="rules">
        <span class="muted">示例规则版本：</span>
        <el-tag v-for="r in samples.sample_rule_versions" :key="r.label" size="small" style="margin-right:6px">
          {{ r.label }} · {{ r.factor }} · v{{ r.version }}
        </el-tag>
      </div>
    </el-card>

    <el-row :gutter="12">
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>回放运行</span></template>
          <el-table :data="runs" size="small" border highlight-current-row @current-change="pickRun">
            <el-table-column prop="id" label="#" width="60" />
            <el-table-column prop="batch_code" label="批次" width="150" />
            <el-table-column prop="defects" label="缺陷" width="70" />
            <el-table-column prop="alert_delta" label="告警影响" width="90" />
            <el-table-column prop="corrections_created" label="补正" width="70" />
            <el-table-column prop="finished_at" label="完成(UTC)" width="170" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="mb">
          <template #header><span>受影响输入 / 撤销检测</span></template>
          <div v-if="lastAffected.length">
            <el-alert v-for="(a, i) in lastAffected" :key="i" type="warning" :closable="false" class="mb"
              :title="`${a.rule_code} v${a.revoked_version}：${a.reason}`"
              description="沙箱已标出受影响输入并禁止以替代版本静默覆盖，须显式复核。" />
          </div>
          <el-empty v-else description="无撤销影响" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="selectedRun">
      <template #header><span>回放结果 —— 运行 #{{ selectedRun.id }}（新旧质量结论差异与告警影响）</span></template>
      <el-table :data="results" size="small" border max-height="380">
        <el-table-column prop="device_point" label="点位" width="110" />
        <el-table-column prop="factor" label="因子" width="80" />
        <el-table-column prop="reported_status" label="报送判定" width="100" />
        <el-table-column label="回放结论" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row.replay_conclusion)">{{ text(row.replay_conclusion) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="diff_class" label="差异" width="140" />
        <el-table-column label="告警影响" width="120">
          <template #default="{ row }">
            <span :class="{ bad: row.alert_impact !== 'none' }">{{ impactText(row.alert_impact) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="说明" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-dialog v-model="readingsDlg" title="只读原始读数样本" width="720px">
      <el-table :data="readings" size="small" border max-height="420">
        <el-table-column prop="device_point" label="点位" width="110" />
        <el-table-column prop="factor" label="因子" width="80" />
        <el-table-column prop="reading_ts" label="读数时间(UTC)" width="180" />
        <el-table-column prop="value" label="数值" width="80" />
        <el-table-column prop="reported_status" label="报送判定" width="100" />
        <el-table-column prop="rule_label" label="规则版本" width="130" />
        <el-table-column prop="device_status" label="设备状态" width="100" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const samples = ref({ batches: [], sample_rule_versions: [] })
const runs = ref([])
const results = ref([])
const selectedRun = ref(null)
const lastAffected = ref([])
const readings = ref([])
const readingsDlg = ref(false)
const busy = ref(false)

function tagType(c) {
  return c === 'normal' ? 'success' : c === 'exceed' ? 'danger' : 'warning'
}
function text(c) {
  return c === 'normal' ? '正常' : c === 'exceed' ? '超标' : c === 'pending_review' ? '待核' : c
}
function impactText(v) {
  return { none: '-', would_alert: '将新增告警', false_alert: '原为误告警' }[v] || v
}

async function load() {
  const [s, r] = await Promise.all([api.get('/replay/samples'), api.get('/replay/runs')])
  samples.value = s.data
  runs.value = r.data
}
async function runReplay(code) {
  busy.value = true
  try {
    const { data } = await api.post('/replay/run', { batch_code: code })
    lastAffected.value = data.affected_inputs || []
    ElMessage.success(`回放完成：缺陷 ${data.defects}，告警影响 ${data.alert_delta}，创建补正 ${data.corrections_created}`)
    await load()
    if (runs.value.length) pickRun(runs.value[0])
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '回放失败')
  } finally {
    busy.value = false
  }
}
async function pickRun(row) {
  if (!row) return
  selectedRun.value = row
  const { data } = await api.get(`/replay/runs/${row.id}/results`)
  results.value = data
}
async function showReadings(code) {
  const { data } = await api.get(`/replay/samples/${code}/readings`)
  readings.value = data
  readingsDlg.value = true
}
onMounted(load)
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.rules { margin-top: 12px; }
.muted { color: #909399; font-size: 12px; }
.sub { color: #909399; font-size: 12px; }
.bad { color: #f56c6c; font-weight: 600; }
</style>

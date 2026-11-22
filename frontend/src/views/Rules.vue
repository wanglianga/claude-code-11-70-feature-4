<template>
  <div>
    <el-alert class="mb" type="info" :closable="false" show-icon
      title="规则目录：记录依赖、可用状态和发布版本。规则维护期间不可执行的读数进入待判队列，恢复后补判，绝不当场判为合格。" />

    <el-row :gutter="12" class="mb">
      <el-col :span="14">
        <el-card>
          <template #header>
            <div class="head">
              <span>路线建议（依据：维护平均跨度 / 日志检索代价 / 需复现监管批次数）</span>
              <el-tag :type="rec.recommended === 'snapshot' ? 'warning' : 'success'" size="small">
                推荐 {{ rec.recommended === 'snapshot' ? '封存快照(snapshot)' : '事件日志重建(event_log)' }}
              </el-tag>
            </div>
          </template>
          <div class="metrics">
            <span>维护平均跨度 <b>{{ rec.avg_maintenance_span_hours ?? '-' }} h</b></span>
            <span>影响分区日志 <b>{{ rec.affected_log_rows ?? '-' }}</b> 行</span>
            <span>需复现监管批次 <b>{{ rec.sample_batch_count ?? '-' }}</b> 个</span>
          </div>
          <ul class="reasons">
            <li v-for="r in rec.reasons" :key="r">{{ r }}</li>
          </ul>
          <div class="cfg">
            <span>默认路线：</span>
            <el-select v-model="cfg.default_strategy" size="small" style="width: 160px">
              <el-option label="封存快照 snapshot" value="snapshot" />
              <el-option label="事件日志 event_log" value="event_log" />
            </el-select>
            <span style="margin-left: 12px">法定补判时限：</span>
            <el-input-number v-model="cfg.legal_deadline_hours" :min="1" :max="168" size="small" />
            <span>h</span>
            <el-button size="small" type="primary" style="margin-left: 12px" @click="saveCfg">保存</el-button>
          </div>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card>
          <template #header><span>开启规则维护</span></template>
          <el-form label-width="96px" size="small">
            <el-form-item label="规则">
              <el-select v-model="maint.rule_code" placeholder="选择规则" style="width: 100%">
                <el-option v-for="r in rules" :key="r.rule_code" :label="`${r.rule_code} · ${r.name}`" :value="r.rule_code" />
              </el-select>
            </el-form-item>
            <el-form-item label="路线">
              <el-radio-group v-model="maint.strategy">
                <el-radio value="snapshot">封存快照</el-radio>
                <el-radio value="event_log">事件日志重建</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="影响分区">
              <el-select v-model="maint.partitions" multiple placeholder="可留空=全部" style="width: 100%">
                <el-option v-for="p in ['P-HIGH-1','P-HIGH-2','P-MED-1','P-LOW-1']" :key="p" :label="p" :value="p" />
              </el-select>
            </el-form-item>
            <el-form-item label="原因"><el-input v-model="maint.reason" /></el-form-item>
            <el-button type="warning" :loading="busy" @click="openMaint">开启维护（规则置不可用）</el-button>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mb">
      <template #header><span>规则目录</span></template>
      <el-table :data="rules" size="small" border>
        <el-table-column prop="rule_code" label="规则编码" width="170" />
        <el-table-column prop="name" label="名称" width="170" />
        <el-table-column prop="factor" label="因子" width="100" />
        <el-table-column prop="check_dimension" label="维度" width="120" />
        <el-table-column label="依赖" width="200">
          <template #default="{ row }">
            <el-tag v-for="d in row.dependencies" :key="d" size="small" type="info" style="margin-right:4px">{{ d }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="current_version" label="当前版本" width="90" />
        <el-table-column label="可用状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.available ? 'success' : 'danger'" size="small">{{ row.available ? '可用' : '不可用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="维护窗口" width="170">
          <template #default="{ row }">
            <span v-if="row.maintenance">#{{ row.maintenance.id }} · {{ row.maintenance.strategy }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="loadVersions(row.rule_code)">版本历史</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="mb">
      <template #header><span>维护窗口</span></template>
      <el-table :data="windows" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="rule_code" label="规则" width="160" />
        <el-table-column prop="factor" label="因子" width="90" />
        <el-table-column label="路线" width="120">
          <template #default="{ row }">{{ row.strategy === 'snapshot' ? '封存快照' : '事件日志' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'danger' : 'info'" size="small">{{ row.status === 'active' ? '维护中' : '已关闭' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="pending_count" label="待判" width="70" />
        <el-table-column prop="rejudged_count" label="已补判" width="80" />
        <el-table-column prop="span_hours" label="跨度(h)" width="90" />
        <el-table-column prop="started_at" label="开始(UTC)" width="180" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button v-if="row.status === 'active'" size="small" link type="primary" @click="closeMaint(row.id)">关闭维护</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="versionsFor">
      <template #header><span>版本历史 —— {{ versionsFor }}（撤销版本需显式标记，不允许静默覆盖）</span></template>
      <el-table :data="versions" size="small" border>
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.revoked ? 'danger' : 'success'" size="small">{{ row.revoked ? '已撤销' : row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="revoked_reason" label="撤销原因" show-overflow-tooltip />
        <el-table-column prop="published_at" label="发布时间(UTC)" width="180" />
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button v-if="!row.revoked" size="small" link type="danger" @click="revoke(row.version)">撤销此版本</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="publish">
        <el-input v-model="newParams" placeholder='新版本参数 JSON，如 {"threshold_max": 95}' size="small" style="width: 360px" />
        <el-button size="small" type="primary" @click="publish">发布新版本</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const rules = ref([])
const windows = ref([])
const versions = ref([])
const versionsFor = ref('')
const rec = ref({})
const cfg = reactive({ default_strategy: 'snapshot', legal_deadline_hours: 24 })
const maint = reactive({ rule_code: '', strategy: 'snapshot', partitions: [], reason: '夜间维护' })
const newParams = ref('{"threshold_max": 95}')
const busy = ref(false)

async function load() {
  const { data } = await api.get('/rules/catalog')
  rules.value = data.rules
  rec.value = data.strategy_recommendation
  Object.assign(cfg, data.pending_config)
  const w = await api.get('/rules/maintenance')
  windows.value = w.data
}
async function loadVersions(code) {
  versionsFor.value = code
  const { data } = await api.get(`/rules/${code}/versions`)
  versions.value = data
}
async function saveCfg() {
  await api.post('/rules/pending-config', { ...cfg })
  ElMessage.success('待判配置已保存')
  load()
}
async function openMaint() {
  if (!maint.rule_code) return ElMessage.warning('请选择规则')
  busy.value = true
  try {
    await api.post('/rules/maintenance', { ...maint })
    ElMessage.success('已开启维护，规则置为不可用')
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '开启失败')
  } finally {
    busy.value = false
  }
}
async function closeMaint(id) {
  await api.post(`/rules/maintenance/${id}/close`)
  ElMessage.success('维护窗口已关闭，规则恢复可用，等待补判')
  load()
}
async function revoke(version) {
  const { value } = await ElMessageBox.prompt('撤销原因（会阻止静默以替代版本覆盖）', '撤销规则版本', {
    inputValue: '维护中途撤销',
  })
  await api.post(`/rules/${versionsFor.value}/versions/${version}/revoke`, { reason: value })
  ElMessage.success(`已撤销 v${version}`)
  loadVersions(versionsFor.value)
  load()
}
async function publish() {
  let params = {}
  try {
    params = JSON.parse(newParams.value)
  } catch {
    return ElMessage.error('参数 JSON 无效')
  }
  await api.post(`/rules/${versionsFor.value}/versions`, { params })
  ElMessage.success('已发布新版本')
  loadVersions(versionsFor.value)
  load()
}
onMounted(load)
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.metrics { display: flex; gap: 24px; margin-bottom: 8px; color: #606266; }
.reasons { margin: 0 0 12px; padding-left: 18px; color: #909399; font-size: 12px; }
.cfg { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.publish { margin-top: 12px; display: flex; gap: 8px; }
</style>

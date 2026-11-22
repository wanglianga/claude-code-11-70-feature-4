<template>
  <div>
    <el-alert class="mb" type="warning" :closable="false" show-icon
      title="影响通知册：回放发现只读样本中已报送读数缺陷时创建待补正事项，把待补正的值班记录与监管交付逐项关联；保留原报送范围、回执与当时依据，不覆盖原样本。" />

    <el-row :gutter="12" class="mb">
      <el-col :span="6"><el-card><div class="num">{{ sum.total ?? '-' }}</div><div class="lbl">补正事项</div></el-card></el-col>
      <el-col :span="6"><el-card><div class="num bad">{{ sum.open ?? '-' }}</div><div class="lbl">待处理</div></el-card></el-col>
      <el-col :span="6"><el-card><div class="num warn">{{ sum.acknowledged ?? '-' }}</div><div class="lbl">已确认</div></el-card></el-col>
      <el-col :span="6"><el-card><div class="num ok">{{ sum.closed ?? '-' }}</div><div class="lbl">已闭合</div></el-card></el-col>
    </el-row>

    <el-card>
      <template #header>
        <div class="head">
          <span>未闭合补正清单</span>
          <el-radio-group v-model="statusFilter" size="small" @change="load">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="open">待处理</el-radio-button>
            <el-radio-button value="acknowledged">已确认</el-radio-button>
            <el-radio-button value="closed">已闭合</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-table :data="items" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="监管交付" min-width="220">
          <template #default="{ row }">
            <div>{{ row.regulatory_delivery.regulator }}</div>
            <div class="sub">批次 {{ row.batch_code }} · 回执 {{ row.regulatory_delivery.receipt_no }}</div>
            <div class="sub">{{ row.original_scope }}</div>
          </template>
        </el-table-column>
        <el-table-column label="值班记录" width="170">
          <template #default="{ row }">
            <div v-if="row.duty_record">{{ row.duty_record.shift_label }}</div>
            <div v-if="row.duty_record" class="sub">{{ row.duty_record.operator }}</div>
            <span v-else class="sub">-</span>
          </template>
        </el-table-column>
        <el-table-column label="缺陷读数" width="160">
          <template #default="{ row }">
            <div>{{ row.device_point }}</div>
            <div class="sub">{{ row.factor }} · {{ row.reading_ts }}</div>
          </template>
        </el-table-column>
        <el-table-column label="当时依据" min-width="180">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.original_basis?.reported_status }} · {{ row.original_basis?.rule_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="issue" label="发现的问题" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="stType(row.status)">{{ stText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170">
          <template #default="{ row }">
            <el-button v-if="row.status === 'open'" size="small" link type="primary" @click="setStatus(row, 'acknowledged')">确认收到</el-button>
            <el-button v-if="row.status !== 'closed'" size="small" link type="success" @click="setStatus(row, 'closed')">闭合</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const items = ref([])
const sum = ref({})
const statusFilter = ref('')

function stType(s) {
  return s === 'closed' ? 'success' : s === 'acknowledged' ? 'warning' : 'danger'
}
function stText(s) {
  return s === 'closed' ? '已闭合' : s === 'acknowledged' ? '已确认' : '待处理'
}

async function load() {
  const params = statusFilter.value ? { status: statusFilter.value } : {}
  const { data } = await api.get('/corrections', { params })
  items.value = data.items
  sum.value = data.summary
}
async function setStatus(row, status) {
  await api.post(`/corrections/${row.id}/status`, { status })
  ElMessage.success(status === 'closed' ? '已闭合（保留原报送范围与依据）' : '已确认收到')
  load()
}
onMounted(load)
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.num { font-size: 26px; font-weight: 600; color: #303133; }
.num.ok { color: #67c23a; }
.num.warn { color: #e6a23c; }
.num.bad { color: #f56c6c; }
.lbl { color: #909399; font-size: 12px; margin-top: 4px; }
.sub { color: #909399; font-size: 12px; }
</style>
